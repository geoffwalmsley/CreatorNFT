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

from chia.wallet.derive_keys import master_sk_to_wallet_sk
from chia.types.coin_spend import CoinSpend
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles import singleton_top_layer
from chia.types.announcement import Announcement

from CreatorNFT.sim import load_clsp_relative
from CreatorNFT.sim import setup_node_only
from CreatorNFT.driver import make_launcher_spend, make_found_spend, make_eve_spend, make_buy_spend, make_update_spend


SINGLETON_MOD = load_clvm("singleton_top_layer.clvm")
SINGLETON_MOD_HASH = SINGLETON_MOD.get_tree_hash()
LAUNCHER_PUZZLE = load_clvm("singleton_launcher.clvm")
LAUNCHER_PUZZLE_HASH = LAUNCHER_PUZZLE.get_tree_hash()

ESCAPE_VALUE = -113
MELT_CONDITION = [ConditionOpcode.CREATE_COIN, 0, ESCAPE_VALUE]

INNER_MOD = load_clsp_relative("clsp/singleton_payer.clsp")
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
        singleton = load_clsp_relative("clsp/singleton_payer.clsp")
        p2 = load_clsp_relative("clsp/p2_singleton_payer.clsp")
        assert singleton
        assert p2

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
        royalty = [alice.puzzle_hash, 10]
        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        # Launcher Spend
        launcher_spend = make_launcher_spend(found_coin, amount, launch_state, royalty, nft_data)
        found_spend = make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
        eve_spend = make_eve_spend(launch_state, royalty, launcher_spend)

        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        assert res["additions"]
        assert res["removals"]

        nft_coin_record = await node.sim_client.get_coin_records_by_parent_ids([eve_spend.coin.name()])
        nft_coin = nft_coin_record[0].coin

        # Purchase Spend
        new_state = [0, 0, bob.puzzle_hash, bob.pk_]
        price = 1000
        payment_coin = await bob.choose_coin(price)
        payment_coin_puzzle = puzzle_for_pk(bob.pk_)
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_record[0].confirmed_block_index
        )
        nft_spend, p2_spend, payment_spend = make_buy_spend(
            nft_coin, new_state, payment_coin, payment_coin_puzzle, launcher_coin, last_spend
        )

        sb = await sign_coin_spends(
            [nft_spend, p2_spend, payment_spend],
            bob.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )
        alice_pre_balance = alice.balance()
        bob_pre_balance = bob.balance()

        res_2 = await node.push_tx(sb)
        assert res_2["additions"]
        assert res_2["removals"]

        # assert alice has been paid
        assert alice_pre_balance + price == alice.balance()
        assert bob_pre_balance - price == bob.balance()

        # assert latest amount in nft is unchanged
        nft_coin_record = await node.sim_client.get_coin_records_by_parent_ids([nft_coin.name()])
        assert len(nft_coin_record) == 3
        nft_coin_record = next(r for r in nft_coin_record if r.coin.amount == nft_coin.amount)
        assert not nft_coin_record.spent
        nft_coin = nft_coin_record.coin

        # assert that the current state is not for sale
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_record.confirmed_block_index
        )
        p, _ = last_spend.solution.to_program().uncurry()

        state = p.as_python()[-1][0]
        assert int_from_bytes(state[0]) != 100

        # bob updates state to for sale
        new_state = [100, 1200, bob.puzzle_hash, bob.pk_]
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_record.confirmed_block_index
        )
        update_spend = make_update_spend(nft_coin, launcher_coin, new_state, last_spend)

        sb = await sign_coin_spends(
            [update_spend],
            bob.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )
        res = await node.push_tx(sb)
        assert len(res["additions"]) == 1

        nft_coin = res["additions"][0]

        nft_coin_records = await node.sim_client.get_coin_records_by_parent_ids([nft_coin.parent_coin_info])

        assert not nft_coin_records[0].spent

        # assert that the current state is for sale
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_records[0].confirmed_block_index
        )
        p, _ = last_spend.solution.to_program().uncurry()

        state = p.as_python()[-1][0]
        assert int_from_bytes(state[0]) == 100

        # carol purchases
        new_state = [0, 1200, carol.puzzle_hash, carol.pk_]
        price = 1200
        payment_coin = await carol.choose_coin(price)
        payment_coin_puzzle = puzzle_for_pk(carol.pk_)

        nft_spend, p2_spend, payment_spend = make_buy_spend(
            nft_coin, new_state, payment_coin, payment_coin_puzzle, launcher_coin, last_spend
        )

        sb = await sign_coin_spends(
            [nft_spend, p2_spend, payment_spend],
            carol.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        alice_pre_balance = alice.balance()
        bob_pre_balance = bob.balance()
        carol_pre_balance = carol.balance()

        res = await node.push_tx(sb)

        assert res["additions"]

        # assert royalty payments are made
        assert alice.balance() == alice_pre_balance + (price * royalty[1] / 100)
        assert bob.balance() == bob_pre_balance + (price * (100 - royalty[1]) / 100)
        assert carol.balance() == carol_pre_balance - price

    @pytest.mark.asyncio
    async def test_zero_royalty(self, node, alice, bob, carol):
        amount = 101
        nft_data = ("CreatorNFT", ["v0.1", "other data", "three"])
        launch_state = [100, 1000, alice.puzzle_hash, alice.pk_]
        royalty = [alice.puzzle_hash, 0]
        found_coin = await alice.choose_coin(amount)
        found_coin_puzzle = puzzle_for_pk(alice.pk_)
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        # Launcher Spend
        launcher_spend = make_launcher_spend(found_coin, amount, launch_state, royalty, nft_data)
        found_spend = make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
        eve_spend = make_eve_spend(launch_state, royalty, launcher_spend)

        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            alice.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await node.push_tx(sb)
        assert res["additions"]
        assert res["removals"]

        nft_coin_record = await node.sim_client.get_coin_records_by_parent_ids([eve_spend.coin.name()])
        nft_coin = nft_coin_record[0].coin

        # Purchase Spend
        new_state = [0, 0, bob.puzzle_hash, bob.pk_]
        price = 1000
        payment_coin = await bob.choose_coin(price)
        payment_coin_puzzle = puzzle_for_pk(bob.pk_)
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_record[0].confirmed_block_index
        )
        nft_spend, p2_spend, payment_spend = make_buy_spend(
            nft_coin, new_state, payment_coin, payment_coin_puzzle, launcher_coin, last_spend
        )

        sb = await sign_coin_spends(
            [nft_spend, p2_spend, payment_spend],
            bob.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )
        alice_pre_balance = alice.balance()
        bob_pre_balance = bob.balance()

        res_2 = await node.push_tx(sb)
        assert res_2["additions"]
        assert res_2["removals"]

        # assert alice has been paid
        assert alice_pre_balance + price == alice.balance()
        assert bob_pre_balance - price == bob.balance()

        # assert latest amount in nft is unchanged
        nft_coin_record = await node.sim_client.get_coin_records_by_parent_ids([nft_coin.name()])
        assert len(nft_coin_record) == 3
        nft_coin_record = next(r for r in nft_coin_record if r.coin.amount == nft_coin.amount)
        assert not nft_coin_record.spent
        nft_coin = nft_coin_record.coin

        # assert that the current state is not for sale
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_record.confirmed_block_index
        )
        p, _ = last_spend.solution.to_program().uncurry()

        state = p.as_python()[-1][0]
        assert int_from_bytes(state[0]) != 100

        # bob updates state to for sale
        new_state = [100, 1200, bob.puzzle_hash, bob.pk_]
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_record.confirmed_block_index
        )
        update_spend = make_update_spend(nft_coin, launcher_coin, new_state, last_spend)

        sb = await sign_coin_spends(
            [update_spend],
            bob.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )
        res = await node.push_tx(sb)
        assert len(res["additions"]) == 1

        nft_coin = res["additions"][0]

        nft_coin_records = await node.sim_client.get_coin_records_by_parent_ids([nft_coin.parent_coin_info])

        assert not nft_coin_records[0].spent

        # assert that the current state is for sale
        last_spend = await node.sim_client.get_puzzle_and_solution(
            nft_coin.parent_coin_info, nft_coin_records[0].confirmed_block_index
        )
        p, _ = last_spend.solution.to_program().uncurry()

        state = p.as_python()[-1][0]
        assert int_from_bytes(state[0]) == 100

        # carol purchases
        new_state = [0, 1200, carol.puzzle_hash, carol.pk_]
        price = 1200
        payment_coin = await carol.choose_coin(price)
        payment_coin_puzzle = puzzle_for_pk(carol.pk_)

        nft_spend, p2_spend, payment_spend = make_buy_spend(
            nft_coin, new_state, payment_coin, payment_coin_puzzle, launcher_coin, last_spend
        )

        sb = await sign_coin_spends(
            [nft_spend, p2_spend, payment_spend],
            carol.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        alice_pre_balance = alice.balance()
        bob_pre_balance = bob.balance()
        carol_pre_balance = carol.balance()

        res = await node.push_tx(sb)

        assert res["additions"]

        # assert royalty payments are made
        assert alice.balance() == alice_pre_balance
        assert bob.balance() == bob_pre_balance + price
        assert carol.balance() == carol_pre_balance - price
