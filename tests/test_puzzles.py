
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

INNER_MOD = load_clsp_relative("clsp/nft_with_fee.clsp")
P2_MOD = load_clsp_relative("clsp/p2_singleton_payer.clsp")


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
        singleton = load_clsp_relative("clsp/nft_with_fee.clsp")
        p2 = load_clsp_relative("clsp/p2_singleton_payer.clsp")
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
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH,
                                           (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)),
                                           curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to([full_puzzle.get_tree_hash(),
                               SINGLETON_MOD_HASH,
                               launcher_id,
                               LAUNCHER_PUZZLE_HASH,
                               INNER_MOD.get_tree_hash(),
                               state,
                               royalty_good,
                               amount,
                               key_value_list])
        
        conds = LAUNCHER_PUZZLE.run(solution)
        assert conds.as_python()[0][1] == nft_full_puzzle_hash

        
        # TEST NEGATIVE ROYALTY
        args = [INNER_MOD.get_tree_hash(), state, royalty_neg]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH,
                                           (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)),
                                           curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to([full_puzzle.get_tree_hash(),
                               SINGLETON_MOD_HASH,
                               launcher_id,
                               LAUNCHER_PUZZLE_HASH,
                               INNER_MOD.get_tree_hash(),
                               state,
                               royalty_neg,
                               amount,
                               key_value_list])

        with pytest.raises(EvalError) as e:
            conds = LAUNCHER_PUZZLE.run(solution)

        sexp = e.value._sexp
        p = Program.to(sexp)
        msg = p.first().as_python()
        assert msg == b'royalty < 0'

        # TEST > 100 ROYALTY
        args = [INNER_MOD.get_tree_hash(), state, royalty_big]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH,
                                           (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)),
                                           curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to([full_puzzle.get_tree_hash(),
                               SINGLETON_MOD_HASH,
                               launcher_id,
                               LAUNCHER_PUZZLE_HASH,
                               INNER_MOD.get_tree_hash(),
                               state,
                               royalty_big,
                               amount,
                               key_value_list])

        with pytest.raises(EvalError) as e:
            conds = LAUNCHER_PUZZLE.run(solution)

        sexp = e.value._sexp
        p = Program.to(sexp)
        msg = p.first().as_python()
        assert msg == b'royalty > 100'

        # TEST LAUNCH TO DODGY PUZHASH
        args = [INNER_MOD.get_tree_hash(), state, royalty_good]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH,
                                           (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)),
                                           curried)
        nft_full_puzzle_hash = full_puzzle.get_tree_hash()
        launcher_id = launcher_coin.name()

        solution = Program.to([bytes(b"a" * 32),
                               SINGLETON_MOD_HASH,
                               launcher_id,
                               LAUNCHER_PUZZLE_HASH,
                               INNER_MOD.get_tree_hash(),
                               state,
                               royalty_good,
                               amount,
                               key_value_list])

        with pytest.raises(EvalError) as e:
            conds = LAUNCHER_PUZZLE.run(solution)

        sexp = e.value._sexp
        p = Program.to(sexp)
        msg = p.first().as_python()
        assert msg == b'incorrect inner puzzle'        
    
    @pytest.mark.asyncio
    async def test_launcher_spend(self, node, alice):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [100, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 25]

        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        args = [INNER_MOD.get_tree_hash(), state, royalty]
        curried = INNER_MOD.curry(*args)
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH,
                                           (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)),
                                           curried)
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

        found_conds = driver.run_singleton(found_spend.puzzle_reveal.to_program(),
                                           found_spend.solution.to_program())

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
        full_puzzle = SINGLETON_MOD.curry((SINGLETON_MOD_HASH,
                                           (launcher_coin.name(), LAUNCHER_PUZZLE_HASH)),
                                           curried)
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
        assert res['additions']

        
    @pytest.mark.asyncio
    async def test_update_spend(self, node, alice, bob):
        amount = 101
        key_value_list = ("CreatorNFT", ["v0.1", "other data", "three"])
        state = [100, 1000, alice.puzzle_hash, alice.pk_]
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
        assert res['additions']

        # make update spend
        nft_coin = next(c for c in res['additions'] if (c.amount == amount) and (c.parent_coin_info == eve_spend.coin.name()))
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
        assert res['additions']

        # update again to ensure lineage proof is right
        assert len(res['additions']) == 1

        nft_coin = res['additions'][0]
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
        assert res['additions']

        



    @pytest.mark.asyncio
    async def test_lifecycle(self, node, alice, bob, carol):
        """The test process is:
        Launch nnft with unlocked state, and do eve spend during the launch to make
        the current puzzle state discovreable from the last_spend solution
        Purchase by bob, recurried to locked state with bobs creds, both payments to alice
        Attempted purchase by carol should fail (assert for sale state is not 100)
        bob update to for sale (assert for sale state is 100)
        carol purchase - royalty accrues to alice, payment goes to bob
        """
        amount = 101
        nft_data = ("CreatorNFT", ["v0.1", "other data", "three"])
        launch_state = [100, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 0]

        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        # Launcher Spend
     
