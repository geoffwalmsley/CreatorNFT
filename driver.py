import asyncio
from typing import Dict, List, Tuple, Optional, Union, Any

from chia.types.blockchain_format.coin import Coin
from chia.types.spend_bundle import SpendBundle
from chia.types.blockchain_format.program import Program, SerializedProgram
from chia.util.hash import std_hash
from clvm.casts import int_to_bytes, int_from_bytes
from chia.util.byte_types import hexstr_to_bytes
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.wallet.puzzles.load_clvm import load_clvm
from chia.util.condition_tools import ConditionOpcode
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (  # standard_transaction
    puzzle_for_pk,
    calculate_synthetic_secret_key,
    DEFAULT_HIDDEN_PUZZLE_HASH,
)
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.wallet.derive_keys import master_sk_to_wallet_sk
from chia.types.coin_spend import CoinSpend
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles import singleton_top_layer
from chia.types.announcement import Announcement

from sim import load_clsp_relative
from nft_wallet import NFT

SINGLETON_MOD = load_clvm("singleton_top_layer.clvm")
SINGLETON_MOD_HASH = SINGLETON_MOD.get_tree_hash()
LAUNCHER_PUZZLE = load_clvm("singleton_launcher.clvm")
LAUNCHER_PUZZLE_HASH = LAUNCHER_PUZZLE.get_tree_hash()

ESCAPE_VALUE = -113
MELT_CONDITION = [ConditionOpcode.CREATE_COIN, 0, ESCAPE_VALUE]

INNER_MOD = load_clsp_relative("clsp/singleton_payer.clsp")
P2_MOD = load_clsp_relative("clsp/p2_singleton_payer.clsp")


def run_singleton(full_puzzle, solution):
    k = full_puzzle.run(solution)
    conds = []
    for x in k.as_iter():
        code = int.from_bytes(x.first(), "big")

        if code == 51:
            ph = x.rest().first().as_python()
            amt = int.from_bytes(x.rest().rest().first().as_python(), "big")
            conds.append([code, ph, amt])
        elif code == 50:
            pk = x.rest().first().as_python()
            msg = x.rest().rest().first().as_python()
            conds.append([code, pk, msg])
        elif code == 61:
            a_id = x.rest().first().as_python().hex()
            conds.append([code, a_id])
        elif code in [60, 62, 63, 70]:
            msg = x.rest().first().as_python()
            conds.append([code, msg])

    return conds


def make_inner(state, royalty):
    args = [INNER_MOD.get_tree_hash(), state, royalty]
    return INNER_MOD.curry(*args)


def make_solution(new_state, payment_info):
    return [new_state, payment_info]


def get_eve_coin_from_launcher(launcher_spend):
    conds = run_singleton(launcher_spend.puzzle_reveal.to_program(), launcher_spend.solution.to_program())
    create_cond = next(c for c in conds if c[0] == 51)
    return Coin(launcher_spend.coin.name(), create_cond[1], create_cond[2])


def make_launcher_spend(found_coin: Coin, amount: int, state: List[Any], royalty: List[Any], key_value_list: Tuple):
    # key_value_list must be a tuple, which can contain lists, but the top-level
    # must be 2 elements
    launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)
    args = [INNER_MOD.get_tree_hash(), state, royalty]
    curried = INNER_MOD.curry(*args)
    full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), curried)
    solution = Program.to([full_puzzle.get_tree_hash(), amount, key_value_list])

    return CoinSpend(launcher_coin, LAUNCHER_PUZZLE, solution)


def make_found_spend(
    found_coin: Coin, found_coin_puzzle: Program, launcher_coin_spend: CoinSpend, amount: int
) -> CoinSpend:
    launcher_announcement = Announcement(launcher_coin_spend.coin.name(), launcher_coin_spend.solution.get_tree_hash())

    conditions = [
        [
            ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT,
            std_hash(launcher_coin_spend.coin.name() + launcher_announcement.message),
        ],
        [ConditionOpcode.CREATE_COIN, launcher_coin_spend.coin.puzzle_hash, amount],
        [ConditionOpcode.CREATE_COIN, found_coin.puzzle_hash, found_coin.amount - amount],
    ]
    delegated_puzzle = Program.to((1, conditions))
    found_coin_solution = Program.to([[], delegated_puzzle, []])
    return CoinSpend(found_coin, found_coin_puzzle, found_coin_solution)


def make_eve_spend(state: List, royalty: List, launcher_spend: CoinSpend):
    eve_coin = get_eve_coin_from_launcher(launcher_spend)
    args = [INNER_MOD.get_tree_hash(), state, royalty]
    eve_inner_puzzle = INNER_MOD.curry(*args)
    full_puzzle = SINGLETON_MOD.curry(
        (SINGLETON_MOD_HASH, (launcher_spend.coin.name(), LAUNCHER_PUZZLE_HASH)), eve_inner_puzzle
    )

    assert full_puzzle.get_tree_hash() == eve_coin.puzzle_hash

    eve_solution = [state, []]
    eve_proof = singleton_top_layer.lineage_proof_for_coinsol(launcher_spend)
    solution = singleton_top_layer.solution_for_singleton(eve_proof, eve_coin.amount, eve_solution)
    eve_spend = CoinSpend(eve_coin, full_puzzle, solution)
    return eve_spend


def uncurry_inner_from_singleton(puzzle: Program):
    _, args = puzzle.uncurry()
    _, inner_puzzle = list(args.as_iter())
    return inner_puzzle


def uncurry_state_and_royalty(puzzle: Program):
    """Uncurry the data from a full singleton puzzle"""
    _, args = puzzle.uncurry()
    _, inner_puzzle = list(args.as_iter())
    _, inner_args = inner_puzzle.uncurry()
    state = inner_args.rest().first().as_python()
    royalty = inner_args.rest().rest().first().as_python()
    return (state, royalty)


def uncurry_solution(solution: Program):
    mod, args = solution.uncurry()
    return mod.as_python()[-1][0]


def make_buy_spend(nft_coin, new_state, payment_coin, payment_coin_puzzle, launcher_coin, last_spend):
    old_state, royalty = uncurry_state_and_royalty(last_spend.puzzle_reveal.to_program())
    current_state = uncurry_solution(last_spend.solution.to_program())
    args = [INNER_MOD.get_tree_hash(), current_state, royalty]
    current_inner_puzzle = INNER_MOD.curry(*args)
    current_singleton_puzzle = SINGLETON_MOD.curry(
        (SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), current_inner_puzzle
    )
    assert current_singleton_puzzle.get_tree_hash() == nft_coin.puzzle_hash
    assert current_state[0] == int_to_bytes(100)
    price = int_from_bytes(current_state[1])

    new_state[0] = 0  # ensure the new singleton is not for sale, move into chialisp?
    new_state[1] = price
    p2_puzzle = P2_MOD.curry(SINGLETON_MOD_HASH, launcher_coin.name(), LAUNCHER_PUZZLE_HASH)
    p2_coin = Coin(payment_coin.name(), p2_puzzle.get_tree_hash(), price)
    lineage_proof = singleton_top_layer.lineage_proof_for_coinsol(last_spend)
    inner_solution = [new_state, p2_coin.name()]
    singleton_solution = singleton_top_layer.solution_for_singleton(lineage_proof, nft_coin.amount, inner_solution)
    p2_solution = Program.to([current_inner_puzzle.get_tree_hash(), p2_coin.name(), new_state])
    delegated_cond = [
        [ConditionOpcode.CREATE_COIN, p2_puzzle.get_tree_hash(), price],
        [ConditionOpcode.CREATE_COIN, payment_coin_puzzle.get_tree_hash(), payment_coin.amount - price],
    ]
    delegated_puz = Program.to((1, delegated_cond))
    delegated_sol = Program.to([[], delegated_puz, []])

    # make coin spends
    nft_spend = CoinSpend(nft_coin, current_singleton_puzzle, singleton_solution)
    p2_spend = CoinSpend(p2_coin, p2_puzzle, p2_solution)
    payment_spend = CoinSpend(payment_coin, payment_coin_puzzle, delegated_sol)
    return (nft_spend, p2_spend, payment_spend)


def make_update_spend_old(nft_coin, launcher_coin, new_state, last_spend):
    old_state, royalty = uncurry_state_and_royalty(last_spend.puzzle_reveal.to_program())
    current_state = uncurry_solution(last_spend.solution.to_program())
    args = [INNER_MOD.get_tree_hash(), current_state, royalty]
    current_inner_puzzle = INNER_MOD.curry(*args)
    current_singleton_puzzle = SINGLETON_MOD.curry(
        (SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), current_inner_puzzle
    )

    assert current_singleton_puzzle.get_tree_hash() == nft_coin.puzzle_hash

    lineage_proof = singleton_top_layer.lineage_proof_for_coinsol(last_spend)
    inner_solution = [new_state, []]
    singleton_solution = singleton_top_layer.solution_for_singleton(
        lineage_proof, last_spend.coin.amount, inner_solution
    )
    return CoinSpend(nft_coin, current_singleton_puzzle, singleton_solution)

def make_update_spend(nft: NFT, new_state):
    old_state, royalty = uncurry_state_and_royalty(nft.last_spend.puzzle_reveal.to_program())
    current_state = uncurry_solution(nft.last_spend.solution.to_program())
    args = [INNER_MOD.get_tree_hash(), nft.state(), nft.royalty()]
    current_inner_puzzle = INNER_MOD.curry(*args)
    current_singleton_puzzle = SINGLETON_MOD.curry(
        (SINGLETON_MOD_HASH, (nft.launcher_id, LAUNCHER_PUZZLE_HASH)), current_inner_puzzle
    )

    assert current_singleton_puzzle.get_tree_hash() == nft.as_coin().puzzle_hash

    lineage_proof = singleton_top_layer.lineage_proof_for_coinsol(nft.last_spend)
    inner_solution = [new_state, []]
    singleton_solution = singleton_top_layer.solution_for_singleton(
        lineage_proof, nft.as_coin().amount, inner_solution
    )
    return CoinSpend(nft.as_coin(), current_singleton_puzzle, singleton_solution)
