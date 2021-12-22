import pytest


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
from chia.types.announcement import Announcement
from chia.wallet.derive_keys import master_sk_to_wallet_sk
from chia.types.coin_spend import CoinSpend
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles import singleton_top_layer
from chia.types.announcement import Announcement

from clvm.EvalError import EvalError

from CreatorNFT.sim import load_clsp_relative
from CreatorNFT.sim import setup_node_only

import CreatorNFT.driver as driver

from CreatorNFT.nft_wallet import NFT


SINGLETON_MOD = load_clvm("singleton_top_layer.clvm")
SINGLETON_MOD_HASH = SINGLETON_MOD.get_tree_hash()
LAUNCHER_PUZZLE = load_clsp_relative("clsp/nft_launcher.clsp")
LAUNCHER_PUZZLE_HASH = LAUNCHER_PUZZLE.get_tree_hash()

ESCAPE_VALUE = -113
MELT_CONDITION = [ConditionOpcode.CREATE_COIN, 0, ESCAPE_VALUE]

INNER_MOD = load_clsp_relative("clsp/creator_nft.clsp")
P2_MOD = load_clsp_relative("clsp/p2_creator_nft.clsp")


@pytest.fixture
async def node():
    node = await setup_node_only()
    yield node
    await node.close()


@pytest.fixture
async def alice(node):
    wallet = node.make_wallet("alice")
    await node.farm_block(farmer=wallet)
    return wallet


@pytest.fixture
async def bob(node):
    wallet = node.make_wallet("bob")
    await node.farm_block(farmer=wallet)
    return wallet


@pytest.fixture
async def carol(node):
    wallet = node.make_wallet("carol")
    await node.farm_block(farmer=wallet)
    return wallet


class TestCreatorNft:
    @pytest.mark.asyncio
    async def test_clsp_compile(self):
        launcher = load_clsp_relative("clsp/nft_launcher.clsp")
        singleton = load_clsp_relative("clsp/creator_nft.clsp")
        p2 = load_clsp_relative("clsp/p2_creator_nft.clsp")
        assert launcher
        assert singleton
        assert p2

    @pytest.mark.asyncio
    async def test_launcher_puzzle(self, node, alice):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [100, 1000, alice.puzzle_hash, alice.pk_]
        royalty_good = [alice.puzzle_hash, 25]
        royalty_neg = [alice.puzzle_hash, -1]
        royalty_big = [alice.puzzle_hash, 101]

        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        # TEST GOOD ROYALTY
        args = [INNER_MOD.get_tree_hash(), state, royalty_good]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to(
            [
                full_puzzle.get_tree_hash(),
                SINGLETON_MOD_HASH,
                launcher_id,
                LAUNCHER_PUZZLE_HASH,
                INNER_MOD.get_tree_hash(),
                state,
                royalty_good,
                amount,
                key_value_list,
            ]
        )

        conds = LAUNCHER_PUZZLE.run(solution)
        assert conds.as_python()[0][1] == nft_full_puzzle_hash

        # TEST NEGATIVE ROYALTY
        args = [INNER_MOD.get_tree_hash(), state, royalty_neg]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to(
            [
                full_puzzle.get_tree_hash(),
                SINGLETON_MOD_HASH,
                launcher_id,
                LAUNCHER_PUZZLE_HASH,
                INNER_MOD.get_tree_hash(),
                state,
                royalty_neg,
                amount,
                key_value_list,
            ]
        )

        with pytest.raises(EvalError) as e:
            conds = LAUNCHER_PUZZLE.run(solution)

        sexp = e.value._sexp
        p = Program.to(sexp)
        msg = p.first().as_python()
        assert msg == b"royalty < 0"

        # TEST > 100 ROYALTY
        args = [INNER_MOD.get_tree_hash(), state, royalty_big]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to(
            [
                full_puzzle.get_tree_hash(),
                SINGLETON_MOD_HASH,
                launcher_id,
                LAUNCHER_PUZZLE_HASH,
                INNER_MOD.get_tree_hash(),
                state,
                royalty_big,
                amount,
                key_value_list,
            ]
        )

        with pytest.raises(EvalError) as e:
            conds = LAUNCHER_PUZZLE.run(solution)

        sexp = e.value._sexp
        p = Program.to(sexp)
        msg = p.first().as_python()
        assert msg == b"royalty > 100"

        # TEST LAUNCH TO DODGY PUZHASH
        args = [INNER_MOD.get_tree_hash(), state, royalty_good]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to(
            [
                bytes(b"a" * 32),
                SINGLETON_MOD_HASH,
                launcher_id,
                LAUNCHER_PUZZLE_HASH,
                INNER_MOD.get_tree_hash(),
                state,
                royalty_good,
                amount,
                key_value_list,
            ]
        )

        with pytest.raises(EvalError) as e:
            conds = LAUNCHER_PUZZLE.run(solution)

        sexp = e.value._sexp
        p = Program.to(sexp)
        msg = p.first().as_python()
        assert msg == b"incorrect inner puzzle"

    @pytest.mark.asyncio
    async def test_launcher_spend(self, node, alice):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [100, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 25]
        fee = 20

        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        args = [INNER_MOD.get_tree_hash(), state, royalty]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()

        # MAKE THE LAUNCHER SPEND
        launcher_spend = driver.make_launcher_spend(found_coin, amount, state, royalty, key_value_list)
        puz = launcher_spend.puzzle_reveal.to_program()
        sol = launcher_spend.solution.to_program()
        conds = driver.run_singleton(puz, sol)

        # Assert that the create_coin puzzlehash is what we expect
        assert conds[0][1] == nft_full_puzzle_hash

        # make the found spend
        found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)

        found_conds = driver.run_singleton(found_spend.puzzle_reveal.to_program(), found_spend.solution.to_program())

        print(type(found_conds[1][1]))
        print(type(std_hash(launcher_coin.name() + conds[1][1])))

        # Assert the coin announcements match
        assert hexstr_to_bytes(found_conds[1][1]) == std_hash(launcher_coin.name() + conds[1][1])

        eve_spend = driver.make_eve_spend(state, royalty, launcher_spend)

        puz = eve_spend.puzzle_reveal.to_program()
        sol = eve_spend.solution.to_program()
        conds = driver.run_singleton(puz, sol)

        # assert the output puzzlehash of eve spend.
        print(conds)
        next_ph = conds[1][1]
        args = [INNER_MOD.get_tree_hash(), state, royalty]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH, (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)), curried)
        print(next_ph)
        print(full_puzzle.get_tree_hash())
        assert full_puzzle.get_tree_hash() == next_ph

        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        print(res)
        assert res["additions"]

    @pytest.mark.asyncio
    async def test_update_spend(self, node, alice, bob):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [100, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 25]
        fee = 20

        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        launcher_spend = driver.make_launcher_spend(found_coin, amount, state, royalty, key_value_list)
        found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
        eve_spend = driver.make_eve_spend(state, royalty, launcher_spend)

        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        assert res["additions"]

        # make update spend
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == eve_spend.coin.name())
        )
        print(nft_coin)
        nft = NFT(launcher_coin.name(), nft_coin, eve_spend, key_value_list, royalty)

        new_state = [0, 10202, alice.puzzle_hash, alice.pk_]
        update_spend = driver.make_update_spend(nft, new_state)

        sb = await sign_coin_spends(
            [update_spend],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        assert res["additions"]

        # update again to ensure lineage proof is right
        assert len(res["additions"]) == 1

        nft_coin = res["additions"][0]
        nft = NFT(launcher_coin.name(), nft_coin, update_spend, key_value_list, royalty)

        new_state = [100, 10202, alice.puzzle_hash, alice.pk_]
        update_spend_2 = driver.make_update_spend(nft, new_state)

        sb = await sign_coin_spends(
            [update_spend_2],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        print(res)
        assert res["additions"]

    @pytest.mark.asyncio
    async def test_launch_and_buy(self, node, alice, bob):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [10, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 25]

        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        launcher_spend = driver.make_launcher_spend(found_coin, amount, state, royalty, key_value_list)
        found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
        eve_spend = driver.make_eve_spend(state, royalty, launcher_spend)

        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        assert res["additions"]

        price = 1000
        payment_coin = await bob.choose_coin(amount)
        payment_coin_puzzle = puzzle_for_pk(bob.pk_)

        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == eve_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, eve_spend, key_value_list, royalty)

        new_state = [0, 10202, bob.puzzle_hash, bob.pk_]

        nft_spend, p2_spend, payment_spend = driver.make_buy_spend(nft, new_state, payment_coin, payment_coin_puzzle)

        sb = await sign_coin_spends(
            [nft_spend, p2_spend, payment_spend],
            bob.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        assert res["additions"]

        # buy a not-for-sale

    @pytest.mark.asyncio
    async def test_buy_not_for_sale(self, node, alice, bob):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [0, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 25]

        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        launcher_spend = driver.make_launcher_spend(found_coin, amount, state, royalty, key_value_list)
        found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
        eve_spend = driver.make_eve_spend(state, royalty, launcher_spend)

        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        assert res["additions"]

        price = 1000
        payment_coin = await bob.choose_coin(amount)
        payment_coin_puzzle = puzzle_for_pk(bob.pk_)

        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == eve_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, eve_spend, key_value_list, royalty)

        new_state = [0, 10202, bob.puzzle_hash, bob.pk_]

        old_state, royalty = driver.uncurry_state_and_royalty(nft.last_spend.puzzle_reveal.to_program())
        current_state = driver.uncurry_solution(nft.last_spend.solution.to_program())
        args = [INNER_MOD.get_tree_hash(), current_state, royalty]

        current_inner_puzzle = INNER_MOD.curry(*args)
        current_singleton_puzzle = SINGLETON_MOD.curry(
            (SINGLETON_MOD_HASH, (nft.launcher_id, LAUNCHER_PUZZLE_HASH)), current_inner_puzzle
        )

        assert current_singleton_puzzle.get_tree_hash() == nft.puzzle_hash
        # assert nft.state()[0] != int_to_bytes(0) # is for sale

        price = int_from_bytes(nft.state()[1])

        p2_puzzle = P2_MOD.curry(SINGLETON_MOD_HASH, nft.launcher_id, LAUNCHER_PUZZLE_HASH)
        p2_coin = Coin(payment_coin.name(), p2_puzzle.get_tree_hash(), price)

        r = nft.last_spend.puzzle_reveal.to_program().uncurry()
        if r is not None:
            _, args = r
            _, inner_puzzle = list(args.as_iter())
            inner_puzzle_hash = inner_puzzle.get_tree_hash()

        lineage_proof = LineageProof(nft.last_spend.coin.parent_coin_info, inner_puzzle_hash, nft.amount)

        # lineage_proof = singleton_top_layer.lineage_proof_for_coinsol(nft.last_spend)

        inner_solution = [new_state, p2_coin.name(), 0]
        singleton_solution = singleton_top_layer.solution_for_singleton(
            lineage_proof, nft.as_coin().amount, inner_solution
        )

        # conds = driver.run_singleton(current_singleton_puzzle, singleton_solution)
        # print(conds)

        p2_solution = Program.to([current_inner_puzzle.get_tree_hash(), p2_coin.name(), new_state])
        delegated_cond = [
            [ConditionOpcode.CREATE_COIN, p2_puzzle.get_tree_hash(), price],
            [ConditionOpcode.CREATE_COIN, payment_coin_puzzle.get_tree_hash(), payment_coin.amount - price],
        ]
        delegated_puz = Program.to((1, delegated_cond))
        delegated_sol = Program.to([[], delegated_puz, []])
        # make coin spends
        nft_spend = CoinSpend(nft.as_coin(), current_singleton_puzzle, singleton_solution)
        p2_spend = CoinSpend(p2_coin, p2_puzzle, p2_solution)
        payment_spend = CoinSpend(payment_coin, payment_coin_puzzle, delegated_sol)

        with pytest.raises(ValueError, match=r"Sign transaction failed*") as e:
            sb = await sign_coin_spends(
                [nft_spend, p2_spend, payment_spend],
                bob.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

    @pytest.mark.asyncio
    async def test_multiple_buys_and_updates(self, node, alice, bob, carol):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [1, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 25]

        async def launch(wallet, amount, state, royalty, key_value_list):
            found_coin = await wallet.choose_coin(amount)
            found_coin_puzzle = puzzle_for_pk(wallet.pk_)
            launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)
            launcher_spend = driver.make_launcher_spend(found_coin, amount, state, royalty, key_value_list)
            found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
            eve_spend = driver.make_eve_spend(state, royalty, launcher_spend)

            sb = await sign_coin_spends(
                [launcher_spend, found_spend, eve_spend],
                wallet.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

            res = await node.push_tx(sb)
            return (res, eve_spend, launcher_coin)

        res, eve_spend, launcher_coin = await launch(alice, amount, state, royalty, key_value_list)
        assert res["additions"]

        async def buy(wallet, nft, new_state):
            payment_coin = await wallet.choose_coin(nft.price())
            payment_coin_puzzle = puzzle_for_pk(wallet.pk_)
            nft_spend, p2_spend, payment_spend = driver.make_buy_spend(
                nft, new_state, payment_coin, payment_coin_puzzle
            )
            sb = await sign_coin_spends(
                [nft_spend, p2_spend, payment_spend],
                wallet.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

            res = await node.push_tx(sb)
            return (res, nft_spend)

        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == eve_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, eve_spend, key_value_list, royalty)
        new_state = [0, 1001, bob.puzzle_hash, bob.pk_]
        res, nft_spend = await buy(bob, nft, new_state)
        assert res["additions"]

        async def update(wallet, nft, new_state):
            update_spend = driver.make_update_spend(nft, new_state)

            sb = await sign_coin_spends(
                [update_spend],
                wallet.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

            res = await node.push_tx(sb)
            return (res, update_spend)

        # Bob updates to for sale, and price to 10000
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == nft_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, nft_spend, key_value_list, royalty)
        new_state = [10, 10000, bob.puzzle_hash, bob.pk_]
        res, update_spend = await update(bob, nft, new_state)
        assert res["additions"]

        # Carol buys off bob
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == update_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, update_spend, key_value_list, royalty)
        new_state = [10, 387350000, carol.puzzle_hash, carol.pk_]
        res, nft_spend = await buy(carol, nft, new_state)
        assert res["additions"]

        # Alice buys off carol
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == nft_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, nft_spend, key_value_list, royalty)
        new_state = [0, 100, alice.puzzle_hash, alice.pk_]
        res, nft_spend = await buy(alice, nft, new_state)
        assert res["additions"]

        # Alice updates
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == nft_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, nft_spend, key_value_list, royalty)
        new_state = [10, 10000, alice.puzzle_hash, alice.pk_]
        res, nft_spend = await update(alice, nft, new_state)
        assert res["additions"]

        # carol buys
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == nft_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, nft_spend, key_value_list, royalty)
        new_state = [10, 387350000, carol.puzzle_hash, carol.pk_]
        res, nft_spend = await buy(carol, nft, new_state)
        assert res["additions"]

        # carol upadtes
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == nft_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, nft_spend, key_value_list, royalty)
        new_state = [0, 3800000, carol.puzzle_hash, carol.pk_]
        res, nft_spend = await update(carol, nft, new_state)
        assert res["additions"]

        # buy a zero royalty

        # buy a 100 royalty

    @pytest.mark.asyncio
    async def test_zero_or_100_royalty(self, node, alice, bob, carol):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [1, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 0]

        async def launch(wallet, amount, state, royalty, key_value_list):
            found_coin = await wallet.choose_coin(amount)
            found_coin_puzzle = puzzle_for_pk(wallet.pk_)
            launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)
            launcher_spend = driver.make_launcher_spend(found_coin, amount, state, royalty, key_value_list)
            found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
            eve_spend = driver.make_eve_spend(state, royalty, launcher_spend)

            sb = await sign_coin_spends(
                [launcher_spend, found_spend, eve_spend],
                wallet.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

            res = await node.push_tx(sb)
            return (res, eve_spend, launcher_coin)

        async def buy(wallet, nft, new_state):
            payment_coin = await wallet.choose_coin(nft.price())
            payment_coin_puzzle = puzzle_for_pk(wallet.pk_)
            nft_spend, p2_spend, payment_spend = driver.make_buy_spend(
                nft, new_state, payment_coin, payment_coin_puzzle
            )
            sb = await sign_coin_spends(
                [nft_spend, p2_spend, payment_spend],
                wallet.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

            res = await node.push_tx(sb)
            return (res, nft_spend)

        async def update(wallet, nft, new_state):
            update_spend = driver.make_update_spend(nft, new_state)

            sb = await sign_coin_spends(
                [update_spend],
                wallet.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

            res = await node.push_tx(sb)
            return (res, update_spend)

        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [1, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 0]

        # Launch
        res, eve_spend, launcher_coin = await launch(alice, amount, state, royalty, key_value_list)
        assert res["additions"]

        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == eve_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, eve_spend, key_value_list, royalty)
        new_state = [0, 1001, bob.puzzle_hash, bob.pk_]
        res, nft_spend = await buy(bob, nft, new_state)
        assert res["additions"]

        # Bob updates to for sale, and price to 10000
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == nft_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, nft_spend, key_value_list, royalty)
        new_state = [10, 10000, bob.puzzle_hash, bob.pk_]
        res, update_spend = await update(bob, nft, new_state)
        assert res["additions"]

        # Carol buys off bob
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == update_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, update_spend, key_value_list, royalty)
        new_state = [10, 387350000, carol.puzzle_hash, carol.pk_]
        res, nft_spend = await buy(carol, nft, new_state)
        assert res["additions"]

        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [1, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 100]

        # Launch with 100
        res, eve_spend, launcher_coin = await launch(alice, amount, state, royalty, key_value_list)
        assert res["additions"]

        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == eve_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, eve_spend, key_value_list, royalty)
        new_state = [0, 1001, bob.puzzle_hash, bob.pk_]
        res, nft_spend = await buy(bob, nft, new_state)
        assert res["additions"]

        # Bob updates to for sale, and price to 10000
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == nft_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, nft_spend, key_value_list, royalty)
        new_state = [10, 10000, bob.puzzle_hash, bob.pk_]
        res, update_spend = await update(bob, nft, new_state)
        assert res["additions"]

        # Carol buys off bob
        nft_coin = next(
            c for c in res["additions"] if (c.amount == amount) and (c.parent_coin_info == update_spend.coin.name())
        )

        nft = NFT(launcher_coin.name(), nft_coin, update_spend, key_value_list, royalty)
        new_state = [10, 387350000, carol.puzzle_hash, carol.pk_]
        res, nft_spend = await buy(carol, nft, new_state)
        assert res["additions"]

    @pytest.mark.asyncio
    async def test_divmod(self, node, alice, bob):
        amount = 101
        rlty = 25
        start_price = 23622343
        # start_price = 2000
        current_state = [10, start_price, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, rlty]
        args = [INNER_MOD.get_tree_hash(), current_state, royalty]

        current_inner_puzzle = INNER_MOD.curry(*args)
        current_singleton_puzzle = SINGLETON_MOD.curry(
            (SINGLETON_MOD_HASH, (bytes(b"ok"), LAUNCHER_PUZZLE_HASH)), current_inner_puzzle
        )
        lineage_proof = LineageProof(bytes(b"a" * 32), bytes(b"b" * 32), amount)

        # (r (f (r Truths))))
        truth = Program.to([[], [[101]]])
        new_state = [0, 20020, bob.puzzle_hash, bob.pk_]
        inner_sol = [truth, new_state, bytes(b"a" * 32)]
        k = current_inner_puzzle.run(inner_sol)

        inner_solution = [new_state, bytes(b"2")]
        singleton_solution = singleton_top_layer.solution_for_singleton(lineage_proof, amount, inner_solution)
        conds = driver.run_singleton(current_singleton_puzzle, singleton_solution)
        assert conds
