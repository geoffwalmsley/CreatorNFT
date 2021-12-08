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
from sim import load_clsp_relative


log = logging.getLogger(__name__)
SINGLETON_MOD = load_clvm("singleton_top_layer.clvm")
SINGLETON_MOD_HASH = SINGLETON_MOD.get_tree_hash()
LAUNCHER_PUZZLE = load_clvm("singleton_launcher.clvm")
LAUNCHER_PUZZLE_HASH = LAUNCHER_PUZZLE.get_tree_hash()
INNER_MOD = load_clsp_relative("clsp/singleton_payer.clsp")
P2_MOD = load_clsp_relative("clsp/p2_singleton_payer.clsp")


class NFT(Coin):
    
    def __init__(self, launcher_id: bytes32, coin: Coin, last_spend: CoinSpend = None):
        super().__init__(coin.parent_coin_info, coin.puzzle_hash, coin.amount)
        self.launcher_id = launcher_id
        self.last_spend = last_spend

    def conditions(self):
        if self.last_spend:
            return conditions_dict_for_solution(self.last_spend.puzzle_reveal.to_program(),
                                                self.last_spend.solution.to_program())

    def state(self):
        mod, args = self.last_spend.solution.to_program().uncurry()
        return mod.as_python()[-1][0]

    def royalty(self):
        _, args = self.last_spend.puzzle_reveal.to_program().uncurry()
        _, inner_puzzle = list(args.as_iter())
        _, inner_args = inner_puzzle.uncurry()
        return inner_args.rest().rest().first().as_python()

    def owner_pk(self):
        return self.state()[-1]

    def owner_puzzle_hash(self):
        return self.state()[-2]
            
    

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
                                       PRIMARY KEY(transition_index, wallet_id))""")
        
        await self.db_connection.execute(
            """CREATE TABLE IF NOT EXISTS
                 nft_coins (launcher_id text PRIMARY KEY,
                           owner_pk text)""")

        await self.db_connection.execute(
            """CREATE TABLE IF NOT EXISTS
                 nft_creators (name text, puzzle_hash text PRIMARY KEY)""")

        await self.db_connection.execute(
            """CREATE TABLE IF NOT EXISTS
                 height (block integer)""")
        
        await self.db_connection.commit()
        # await self.rebuild_cache()
        return self

    async def _clear_database(self):
        cursor = await self.db_connection.execute("DELETE FROM nft_coins")
        await cursor.close()
        await self.db_connection.commit()

    async def add_spend(
        self,
        wallet_id: int,
        spend: CoinSpend,
        height: uint32,
    ) -> None:
        """
        Appends (or replaces) entries in the DB.
        The new list must be at least as long as the existing list, and the
        parent of the first spend must already be present in the DB.
        Note that this is not committed to the DB until db_wrapper.commit() is called.
        However it is written to the cache, so it can be fetched with
        get_all_state_transitions.
        """
        if wallet_id not in self._state_transitions_cache:
            self._state_transitions_cache[wallet_id] = []
        all_state_transitions: List[Tuple[uint32, CoinSpend]] = self.get_spends_for_wallet(wallet_id)

        if (height, spend) in all_state_transitions:
            return

        if len(all_state_transitions) > 0:
            if height < all_state_transitions[-1][0]:
                raise ValueError("Height cannot go down")
            if spend.coin.parent_coin_info != all_state_transitions[-1][1].coin.name():
                raise ValueError("New spend does not extend")

        all_state_transitions.append((height, spend))

        cursor = await self.db_connection.execute(
            "INSERT OR REPLACE INTO nft_state_transitions VALUES (?, ?, ?, ?)",
            (
                len(all_state_transitions) - 1,
                wallet_id,
                height,
                bytes(spend),
            ),
        )
        await cursor.close()

    def get_spends_for_wallet(self, wallet_id: int) -> List[Tuple[uint32, CoinSpend]]:
        """
        Retrieves all entries for a wallet ID from the cache,
        works even if commit is not called yet.
        """
        return self._state_transitions_cache.get(wallet_id, [])

    async def rebuild_cache(self) -> None:
        """
        This resets the cache, and loads all entries from the DB.
        Any entries in the cache that were not committed are removed.
        This can happen if a state transition in wallet_blockchain fails.
        """
        cursor = await self.db_connection.execute(
            "SELECT * FROM nft_state_transitions ORDER BY transition_index")
        rows = await cursor.fetchall()
        await cursor.close()
        self._state_transitions_cache = {}
        for row in rows:
            _, wallet_id, height, coin_spend_bytes = row
            coin_spend: CoinSpend = CoinSpend.from_bytes(coin_spend_bytes)
            if wallet_id not in self._state_transitions_cache:
                self._state_transitions_cache[wallet_id] = []
            self._state_transitions_cache[wallet_id].append((height, coin_spend))

    async def rollback(self, height: int, wallet_id_arg: int) -> None:
        """
        Rollback removes all entries which have entry_height > height passed in.
        Note that this is not committed to the DB until db_wrapper.commit() is called.
        However it is written to the cache, so it can be fetched with
        get_all_state_transitions.
        """
        for wallet_id, items in self._state_transitions_cache.items():
            remove_index_start: Optional[int] = None
            for i, (item_block_height, _) in enumerate(items):
                if item_block_height > height and wallet_id == wallet_id_arg:
                    remove_index_start = i
                    break
            if remove_index_start is not None:
                del items[remove_index_start:]
        cursor = await self.db_connection.execute(
            "DELETE FROM nft_state_transitions WHERE height>? AND wallet_id=?", (height, wallet_id_arg)
        )
        await cursor.close()


    async def get_current_height_from_node(self):
        blockchain_state = await self.node_client.get_blockchain_state()
        new_height = blockchain_state['peak'].height
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

        singletons = await self.node_client.get_coin_records_by_puzzle_hash(LAUNCHER_PUZZLE_HASH, start_height=current_block, end_height=new_height)
        print(len(singletons))
        await self.filter_singletons(singletons)

        while new_height > current_block:
            if new_height - current_block > 1:
                new_height = current_block + 1

            # ADD FUNCTIONS TO UPDATE SINGLE STATES HERE

            await self.set_new_height(new_height)
            current_block = new_height
            blockchain_state = await self.node_client.get_blockchain_state()
            new_height = blockchain_state['peak'].height

            

    async def filter_singletons(self, singletons: List):
        print("starting singleton filter")
        for cr in singletons:
            eve_cr = await self.node_client.get_coin_records_by_parent_ids([cr.coin.name()])
            assert len(eve_cr) > 0
            if eve_cr[0].spent:
                print("getting eve_spend")
                eve_spend = await self.node_client.get_puzzle_and_solution(eve_cr[0].coin.name(),
                                                              eve_cr[0].spent_block_index)
                # uncurry the singletons inner puzzle
                _, args = eve_spend.puzzle_reveal.to_program().uncurry()
                _, inner_puzzle = list(args.as_iter())
                mod, _ = inner_puzzle.uncurry()
                if mod.get_tree_hash() == INNER_MOD.get_tree_hash():
                    print("Found a CreatorNFT")
                    await self.save_launcher(cr.coin.name())

                

    async def get_nft_by_launcher_id(self, launcher_id: bytes32):
        coin_rec = await self.node_client.get_coin_records_by_parent_ids([launcher_id])
        while True:
            if coin_rec[0].spent:
                # get nest coin rec
                last_rec = coin_rec[0]
                coin_rec = await self.node_client.get_coin_records_by_parent_ids(
                                                   [coin_rec[0].coin.name()])
            else:
                # return current and prev
                current_rec = coin_rec[0]
                break
            
        last_spend = await self.node_client.get_puzzle_and_solution(current_rec.coin.parent_coin_info,
                                                                    current_rec.confirmed_block_index)
        
        
        return NFT(launcher_id, coin_rec[0].coin, last_spend)

    async def save_launcher(self, launcher_id, pk=b""):
        cursor = await self.db_connection.execute("INSERT OR REPLACE INTO nft_coins (launcher_id, owner_pk) VALUES (?, ?)", (bytes(launcher_id), bytes(pk)))
        await cursor.close()
        await self.db_connection.commit()
        

    async def save_nft(self, nft: NFT):
        # add launcher_id, owner_pk to db
        cursor = await self.db_connection.execute("INSERT OR REPLACE INTO nft_coins (launcher_id, owner_pk) VALUES (?,?)", (bytes(nft.launcher_id), bytes(nft.owner_pk())))
        await cursor.close()
        await self.db_connection.commit()
        

    async def get_nfts(self, pk: G1Element = None):
        if pk:
            query = f"SELECT launcher_id FROM nft_coins WHERE pk={pk.hex()}"
        else:
            query = "SELECT launcher_id FROM nft_coins"
        cursor = await self.db_connection.execute(query)
        rows = await cursor.fetchall()
        await cursor.close()
        return list(map(lambda x : x[0], rows))
        
    










