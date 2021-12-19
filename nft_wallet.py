import logging
from typing import List, Tuple, Dict, Optional
from blspy import AugSchemeMPL, G1Element, G2Element, PrivateKey
import aiosqlite

from chia.types.blockchain_format.coin import Coin
from chia.types.coin_spend import CoinSpend
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.util.db_wrapper import DBWrapper
from chia.util.ints import uint32
from chia.wallet.puzzles.load_clvm import load_clvm
from clvm.casts import int_to_bytes, int_from_bytes

from sim import load_clsp_relative


log = logging.getLogger(__name__)

SINGLETON_MOD = load_clvm("singleton_top_layer.clvm")
SINGLETON_MOD_HASH = SINGLETON_MOD.get_tree_hash()
LAUNCHER_PUZZLE = load_clsp_relative("clsp/nft_launcher.clsp")
LAUNCHER_PUZZLE_HASH = LAUNCHER_PUZZLE.get_tree_hash()

INNER_MOD = load_clsp_relative("clsp/creator_nft.clsp")
P2_MOD = load_clsp_relative("clsp/p2_creator_nft.clsp")


class NFT(Coin):
    def __init__(self, launcher_id: bytes32, coin: Coin, last_spend: CoinSpend = None, nft_data=None, royalty=None):
        super().__init__(coin.parent_coin_info, coin.puzzle_hash, coin.amount)
        self.launcher_id = launcher_id
        self.last_spend = last_spend
        self.data = nft_data
        self.royalty = royalty

    def conditions(self):
        if self.last_spend:
            return conditions_dict_for_solution(
                self.last_spend.puzzle_reveal.to_program(), self.last_spend.solution.to_program()
            )

    def as_coin(self):
        return Coin(self.parent_coin_info, self.puzzle_hash, self.amount)

    def state(self):
        mod, args = self.last_spend.solution.to_program().uncurry()
        return mod.as_python()[-1][0]

    # def royalty(self):
    #     mod, args = self.last_spend.solution.to_program().uncurry()
    #     return mod.as_python()[0]

    def is_for_sale(self):
        if int_from_bytes(self.state()[0]) != 0:
            return True

    def royalty_pc(self):
        return int_from_bytes(self.royalty[1])

    def owner_pk(self):
        return self.state()[-1]

    def owner_fingerprint(self):
        return G1Element(self.owner_pk()).get_fingerprint()

    def owner_puzzle_hash(self):
        return self.state()[-2]

    def price(self):
        return int_from_bytes(self.state()[1])


class NFTWallet:
    db_connection: aiosqlite.Connection
    db_wrapper: DBWrapper
    _state_transitions_cache: Dict[int, List[Tuple[uint32, CoinSpend]]]

    @classmethod
    async def create(cls, wrapper: DBWrapper, node_client):
        self = cls()

        self.db_connection = wrapper.db
        self.db_wrapper = wrapper
        self.node_client = node_client

        await self.db_connection.execute(
            """CREATE TABLE IF NOT EXISTS
                 nft_state_transitions(transition_index integer,
                                       wallet_id integer,
                                       height bigint,
                                       coin_spend blob,
                                       PRIMARY KEY(transition_index, wallet_id))"""
        )

        await self.db_connection.execute(
            """CREATE TABLE IF NOT EXISTS
                 nft_coins (launcher_id text PRIMARY KEY,
                           owner_pk text)"""
        )

        await self.db_connection.execute(
            """CREATE TABLE IF NOT EXISTS
                 height (block integer)"""
        )

        await self.db_connection.commit()

        return self

    async def _clear_database(self):
        cursor = await self.db_connection.execute("DELETE FROM nft_coins")
        await cursor.close()
        await self.db_connection.commit()

    async def get_current_height_from_node(self):
        blockchain_state = await self.node_client.get_blockchain_state()
        new_height = blockchain_state["peak"].height
        return new_height

    async def set_new_height(self, new_height: int):
        cursor = await self.db_connection.execute("INSERT OR REPLACE INTO height (block) VALUES (?)", (new_height,))
        await cursor.close()
        await self.db_connection.commit()

    async def retrieve_current_block(self):
        current_block = None
        cursor = await self.db_connection.execute("SELECT block FROM height ORDER BY block DESC LIMIT 1")

        returned_block = await cursor.fetchone()
        await cursor.close()

        if returned_block is None:
            current_block = await self.get_current_height_from_node()
            current_block -= 1
        else:
            current_block = returned_block[0]

        return current_block

    async def update_to_current_block(self):
        current_block = await self.retrieve_current_block()
        new_height = await self.get_current_height_from_node()
        if new_height - 1 < current_block:
            current_block = max(new_height - 1, 1)

        if current_block is None:
            current_block = await self.get_current_height_from_node()
            current_block -= 1

        singletons = await self.node_client.get_coin_records_by_puzzle_hash(
            LAUNCHER_PUZZLE_HASH, start_height=current_block, end_height=new_height
        )
        await self.filter_singletons(singletons)

        while new_height > current_block:
            if new_height - current_block > 1:
                new_height = current_block + 1

            # ADD FUNCTIONS TO UPDATE SINGLE STATES HERE

            await self.set_new_height(new_height)
            current_block = new_height
            blockchain_state = await self.node_client.get_blockchain_state()
            new_height = blockchain_state["peak"].height

    async def filter_singletons(self, singletons: List):
        print(f"Updating {len(singletons)} CreatorNFTs")
        for cr in singletons:
            eve_cr = await self.node_client.get_coin_records_by_parent_ids([cr.coin.name()])
            assert len(eve_cr) > 0
            if eve_cr[0].spent:
                eve_spend = await self.node_client.get_puzzle_and_solution(
                    eve_cr[0].coin.name(), eve_cr[0].spent_block_index
                )
                # uncurry the singletons inner puzzle
                _, args = eve_spend.puzzle_reveal.to_program().uncurry()
                _, inner_puzzle = list(args.as_iter())
                mod, _ = inner_puzzle.uncurry()
                if mod.get_tree_hash() == INNER_MOD.get_tree_hash():
                    mod, _ = eve_spend.solution.to_program().uncurry()
                    state = mod.as_python()[-1][0]
                    await self.save_launcher(cr.coin.name(), state[-1])

    async def get_nft_by_launcher_id(self, launcher_id: bytes32):
        nft_id = launcher_id
        launcher_rec = await self.node_client.get_coin_record_by_name(launcher_id)
        launcher_spend = await self.node_client.get_puzzle_and_solution(
            launcher_rec.coin.name(), launcher_rec.spent_block_index
        )
        nft_data = launcher_spend.solution.to_program().uncurry()[0].as_python()[-1]

        while True:
            current_coin_record = await self.node_client.get_coin_record_by_name(nft_id)
            if current_coin_record.spent:
                next_coin_records = await self.node_client.get_coin_records_by_parent_ids([nft_id])
                last_spend = await self.node_client.get_puzzle_and_solution(
                    current_coin_record.coin.name(), current_coin_record.spent_block_index
                )
                if len(next_coin_records) == 3:
                    # last spend was purchase spend, so separate out the puzzlehashes
                    _, args = last_spend.puzzle_reveal.to_program().uncurry()
                    _, inner_puzzle = list(args.as_iter())
                    _, inner_args = inner_puzzle.uncurry()
                    state = inner_args.rest().first().as_python()
                    royalty = inner_args.rest().rest().first().as_python()
                    for rec in next_coin_records:
                        if rec.coin.puzzle_hash not in [state[2], royalty[0]]:
                            next_parent = rec.coin
                if len(next_coin_records) == 1:
                    next_parent = next_coin_records[0].coin
                nft_id = next_parent.name()
                last_coin_record = current_coin_record
            else:
                last_spend = await self.node_client.get_puzzle_and_solution(
                    last_coin_record.coin.name(), last_coin_record.spent_block_index
                )
                _, args = last_spend.puzzle_reveal.to_program().uncurry()
                _, inner_puzzle = list(args.as_iter())
                _, inner_args = inner_puzzle.uncurry()
                # state = inner_args.rest().first().as_python()
                royalty = inner_args.rest().rest().first().as_python()
                nft = NFT(launcher_id, current_coin_record.coin, last_spend, nft_data, royalty)
                await self.save_nft(nft)
                return nft

    async def basic_sync(self):
        all_nfts = await self.node_client.get_coin_records_by_puzzle_hash(LAUNCHER_PUZZLE_HASH)
        await self.filter_singletons(all_nfts)
        await self.update_to_current_block()

    async def save_launcher(self, launcher_id, pk=b""):
        cursor = await self.db_connection.execute(
            "INSERT OR REPLACE INTO nft_coins (launcher_id, owner_pk) VALUES (?, ?)", (bytes(launcher_id), bytes(pk))
        )
        await cursor.close()
        await self.db_connection.commit()

    async def save_nft(self, nft: NFT):
        # add launcher_id, owner_pk to db
        cursor = await self.db_connection.execute(
            "INSERT OR REPLACE INTO nft_coins (launcher_id, owner_pk) VALUES (?,?)",
            (bytes(nft.launcher_id), bytes(nft.owner_pk())),
        )
        await cursor.close()
        await self.db_connection.commit()

    async def get_all_nft_ids(self):
        query = "SELECT launcher_id FROM nft_coins"
        cursor = await self.db_connection.execute(query)
        rows = await cursor.fetchall()
        await cursor.close()
        return list(map(lambda x: x[0], rows))

    async def get_nft_ids_by_pk(self, pk: G1Element = None):
        query = f"SELECT launcher_id FROM nft_coins WHERE owner_pk = ?"
        cursor = await self.db_connection.execute(query, (bytes(pk),))
        rows = await cursor.fetchall()
        await cursor.close()
        return list(map(lambda x: x[0], rows))
