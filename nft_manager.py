import sys
import asyncio
import aiosqlite
from pathlib import Path
import binascii
import sqlite3
from typing import Dict, List, Tuple, Optional, Union, Any
from blspy import AugSchemeMPL, G1Element, G2Element, PrivateKey

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
from chia.util.db_wrapper import DBWrapper
from chia.full_node.coin_store import CoinStore
from chia.wallet.derive_keys import master_sk_to_wallet_sk, master_sk_to_singleton_owner_sk #, master_sk_to_wallet_sk_unhardened
from chia.types.coin_spend import CoinSpend
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles import singleton_top_layer
from chia.types.announcement import Announcement
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.util.default_root import DEFAULT_ROOT_PATH
from chia.rpc.rpc_client import RpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.util.config import load_config
from chia.util.ints import uint16, uint64
from chia.util.bech32m import decode_puzzle_hash, encode_puzzle_hash


from sim import load_clsp_relative
from nft_wallet import NFT, NFTWallet
import driver

SINGLETON_MOD = load_clvm("singleton_top_layer.clvm")
SINGLETON_MOD_HASH = SINGLETON_MOD.get_tree_hash()
LAUNCHER_PUZZLE = load_clvm("singleton_launcher.clvm")
LAUNCHER_PUZZLE_HASH = LAUNCHER_PUZZLE.get_tree_hash()




class NFTManager:
    def __init__(self, wallet_client, node_client, db_name):
        self.wallet_client = wallet_client
        self.node_client = node_client
        self.db_name=db_name
        self.key_dict = {}

    async def connect(self, wallet_index=0):
        if not self.db_name:
            self.db_name = "nft_store.db"
        self.connection = await aiosqlite.connect(Path(self.db_name))
        self.db_wrapper = DBWrapper(self.connection)
        self.nft_wallet = await NFTWallet.create(self.db_wrapper, self.node_client)
        self.fingerprints = await self.wallet_client.get_public_keys()
        fp = self.fingerprints[wallet_index]
        private_key = await self.wallet_client.get_private_key(fp)
        sk_data = binascii.unhexlify(private_key['sk'])
        self.master_sk = PrivateKey.from_bytes(sk_data)
        await self.derive_nft_keys()
        await self.derive_wallet_keys()
        await self.derive_unhardened_keys()
        await self.nft_wallet.update_to_current_block()

    async def close(self):
        if self.node_client:
            self.node_client.close()

        if self.wallet_client:
            self.wallet_client.close()

        if self.connection:
            await self.connection.close()

    async def derive_nft_keys(self, index=0):
        if not self.master_sk:
            await self.load_master_sk()
        _sk = master_sk_to_singleton_owner_sk(self.master_sk, index)
        synth_sk = calculate_synthetic_secret_key(_sk, driver.INNER_MOD.get_tree_hash())
        self.key_dict[bytes(synth_sk.get_g1())] = synth_sk
        self.nft_sk = synth_sk
        self.nft_pk = synth_sk.get_g1()

        
    async def derive_wallet_keys(self, index=0):
        if not self.master_sk:
            await self.load_master_sk()
        _sk = master_sk_to_wallet_sk(self.master_sk, index)
        synth_sk = calculate_synthetic_secret_key(_sk, DEFAULT_HIDDEN_PUZZLE_HASH)
        self.key_dict[bytes(synth_sk.get_g1())] = synth_sk
        self.key_dict[bytes(_sk.get_g1())] = _sk
        self.wallet_sk = _sk


    async def derive_unhardened_keys(self, n=10):
        if not self.master_sk:
            await self.load_master_sk()

        for i in range(n):
            # _sk = master_sk_to_wallet_sk_unhardened(self.master_sk, i)
            _sk = AugSchemeMPL.derive_child_sk_unhardened(self.master_sk, i)
            synth_sk = calculate_synthetic_secret_key(_sk, DEFAULT_HIDDEN_PUZZLE_HASH)
            self.key_dict[bytes(_sk.get_g1())] = _sk
            self.key_dict[bytes(synth_sk.get_g1())] = synth_sk


    async def pk_to_sk(self, pk):
        return self.key_dict.get(bytes(pk))


    async def choose_std_coin(self, amount):
        for k in self.key_dict.keys():
            puzzle = puzzle_for_pk(k)
            my_coins = await self.node_client.get_coin_records_by_puzzle_hash(puzzle.get_tree_hash(), include_spent_coins=False)
            if my_coins:
                coin_record = next((cr for cr in my_coins if (cr.coin.amount >= amount) and (not cr.spent)), None)
                if coin_record:
                    assert not coin_record.spent
                    assert coin_record.coin.puzzle_hash == puzzle.get_tree_hash()
                    synth_sk = calculate_synthetic_secret_key(self.key_dict[k], DEFAULT_HIDDEN_PUZZLE_HASH)
                    self.key_dict[bytes(synth_sk.get_g1())] = synth_sk
                    return (coin_record.coin, puzzle)
        raise ValueError("No spendable coins found")


    async def launch_nft(self, amount, nft_data: Tuple, launch_state: List, royalty: List):
        addr = await self.wallet_client.get_next_address(1, False)
        puzzle_hash = decode_puzzle_hash(addr)
        launch_state += [puzzle_hash, self.nft_pk]
        royalty.insert(0, puzzle_hash)
        
        found_coin, found_coin_puzzle = await self.choose_std_coin(amount)
        
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        launcher_spend = driver.make_launcher_spend(found_coin, amount, launch_state, royalty, nft_data)
        found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
        eve_spend = driver.make_eve_spend(launch_state, royalty, launcher_spend)


        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            self.pk_to_sk,
            DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        )

        res = await self.node_client.push_tx(sb)
        if res['success']:
            # add launcher_id and pk to nft_coins
            await self.nft_wallet.save_launcher(launcher_coin.name(), self.nft_pk)
            tx_id = await self.get_tx_from_mempool(sb.name())
            return (tx_id, launcher_coin.name())


    async def get_tx_from_mempool(self, sb_name):
        # get mempool txn
        mempool_items = await self.node_client.get_all_mempool_items()
        for tx_id in mempool_items.keys():
            mem_sb_name = bytes32(hexstr_to_bytes(mempool_items[tx_id]['spend_bundle_name']))
            if mem_sb_name == sb_name:
                return tx_id
        raise ValueError("No tx found in mempool. Check if confirmed")

        
    async def wait_for_confirmation(self, tx_id, launcher_id):
        while True:
            item = await self.node_client.get_mempool_item_by_tx_id(tx_id)
            if not item:
                return await self.nft_wallet.get_nft_by_launcher_id(launcher_id)
            else:
                print("Waiting for block (30s)")
                await asyncio.sleep(30)

    
    async def update_nft(self, nft_id: bytes, new_state):
        nft = await self.nft_wallet.get_nft_by_launcher_id(nft_id)
        addr = await self.wallet_client.get_next_address(1, False)
        puzzle_hash = decode_puzzle_hash(addr)
        new_state += [puzzle_hash, self.nft_pk]
        update_spend = driver.make_update_spend(nft, new_state)
        conds = driver.run_singleton(update_spend.puzzle_reveal.to_program(), update_spend.solution.to_program())
        target_pk = conds[-1][1]
        
        sb = await sign_coin_spends([update_spend],
                                    self.pk_to_sk,
                                    self.AGG_SIG_ME_DATA,
                                    DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,)
        res = await self.node_client.push_tx(sb)
        if res['success']:
            tx_id = await self.get_tx_from_mempool(sb.name())
            return tx_id

    async def get_my_nfts(self):
        launcher_ids = await self.nft_wallet.get_all_nfts()
        my_nfts = []
        for launcher_id in launcher_ids[:10]:
            nft = await self.nft_wallet.get_nft_by_launcher_id(launcher_id[0])
            if nft.owner_pk() == bytes(self.nft_pk):
                my_nfts.append(nft)
        return my_nfts

    async def get_for_sale_nfts(self):
        await self.nft_wallet.update_to_current_block()
        launcher_ids = await self.nft_wallet.get_all_nfts()
        for_sale_nfts = []
        for launcher_id in launcher_ids[:10]:
            nft = await self.nft_wallet.get_nft_by_launcher_id(launcher_id[0])
            if (nft.is_for_sale()) and (nft.owner_pk() != bytes(self.nft_pk)):
                for_sale_nfts.append(nft)
        return for_sale_nfts


    async def buy_nft(self, launcher_id: bytes, new_state):
        await self.nft_wallet.update_to_current_block()
        nft = await self.nft_wallet.get_nft_by_launcher_id(launcher_id)
        addr = await self.wallet_client.get_next_address(1, False)
        ph = decode_puzzle_hash(addr)
        new_state += [ph, self.nft_pk]
        payment_coin, payment_coin_puzzle = await self.choose_std_coin(nft.price())
        nft_spend, p2_spend, payment_spend = driver.make_buy_spend(nft, new_state, payment_coin, payment_coin_puzzle)
    
        sb = await sign_coin_spends([nft_spend, p2_spend, payment_spend],
                                    self.pk_to_sk,
                                    self.AGG_SIG_ME_DATA,
                                    DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,)
        res = await self.node_client.push_tx(sb)
        if res['success']:
            tx_id = await self.get_tx_from_mempool(sb.name())
            return tx_id


    async def view_nft(self, launcher_id):
        nft = await self.nft_wallet.get_nft_by_launcher_id(launcher_id)
        return nft

    

        
async def main(func):
    # DATA
    amount = 101
    with open(Path("art/bird1.txt"), 'r') as f:
        k = f.readlines()
    data = "".join(k)
    nft_data = ("CreatorNFT", data)
    launch_state = [100, 1000] # append ph and pk later
    royalty = [10]
    
    manager = NFTManager()
    await manager.connect()

    # Update to current block
    await manager.nft_wallet.update_to_current_block()

    # Launch a new NFT
    if func == "launch":
        print("launching")
        tx_id, launcher_id = await manager.launch_nft(amount, nft_data, launch_state, royalty)
        print(f"\nSubmitted tx: {tx_id}")
        nft = await manager.wait_for_confirmation(tx_id, launcher_id)
    
    # List stored NFTs
    if func == "list":
        await manager.nft_wallet.update_to_current_block()
        lids = await manager.get_my_nfts()
        await manager.print_nfts(lids[:5])    
    
    # State update
    if func == "update":
        my_nfts = await manager.get_my_nfts()
        my_nft = await manager.nft_wallet.get_nft_by_launcher_id(my_nfts[-1])
        new_state = [100, 3400, puzzle_for_pk(manager.nft_pk).get_tree_hash(), manager.nft_pk]
        tx_id = await manager.update_nft(my_nft, new_state)
        nft = await manager.wait_for_confirmation(tx_id, my_nft.launcher_id)
        print(nft.state())

    # Find for sale nfts
    if func == "sale":
        print("finding for sale")
        nfts_for_sale = await manager.get_for_sale_nfts()
        for nft in nfts_for_sale:
            print("-"*50)
            print(f"Details for NFT:\n{nft.launcher_id}")
            print(f"Price: {nft.price()}")
            print(f"Royalty: {nft.royalty()}")
            print(f"Data:  {nft.data}")
            print("-*50")

    # Purchase spend (needs second wallet)
    if func == "buy":
        nfts_for_sale = await manager.get_for_sale_nfts()
        if nfts_for_sale:
            nft = nfts_for_sale[-1]
            tx_id = await manager.buy_nft(nft)
            print(f"\n Submitted tx: {tx_id}")

    if func == "test2":
        # nfts = await manager.get_for_sale_nfts()
        nft_id = hexstr_to_bytes("11775d04be2f67da5a531f87e83adf40392fa15b70b8b734a66c278adeae86e5")

        while True:
            current_coin_record = await manager.node_client.get_coin_record_by_name(nft_id)
            if current_coin_record.spent:
                next_coin_records = await manager.node_client.get_coin_records_by_parent_ids([nft_id])
                last_spend = await manager.node_client.get_puzzle_and_solution(current_coin_record.coin.name(), current_coin_record.spent_block_index)
                
                if len(next_coin_records) == 3:
                    # last spend was purchase spend, so separate out the puzzlehashes
                    print("\nPurchase Spend")

                    _, args = last_spend.puzzle_reveal.to_program().uncurry()
                    _, inner_puzzle = list(args.as_iter())
                    _, inner_args = inner_puzzle.uncurry()
                    state = inner_args.rest().first().as_python()
                    royalty = inner_args.rest().rest().first().as_python()
                    print(royalty[1])
                    print(int_from_bytes(state[1]))
                    for rec in next_coin_records:
                        if rec.coin.puzzle_hash not in [state[2], royalty[0]]:
                            next_parent = rec.coin
                            
                if len(next_coin_records) == 1:
                    print("\nUpdate Spend")
                    next_parent = next_coin_records[0].coin
                nft_id = next_parent.name()
                last_coin_record = current_coin_record
            else:
                print("\nGot Unspent")
                last_spend = await manager.node_client.get_puzzle_and_solution(last_coin_record.coin.name(), last_coin_record.spent_block_index)
                _, args = last_spend.puzzle_reveal.to_program().uncurry()
                _, inner_puzzle = list(args.as_iter())
                _, inner_args = inner_puzzle.uncurry()
                state = inner_args.rest().first().as_python()
                royalty = inner_args.rest().rest().first().as_python()
                print(royalty[1])
                print(int_from_bytes(state[1]))
                nft = NFT(nft_id, current_coin_record.coin, last_spend, nft_data, royalty)
                break
            last_puz = last_spend.puzzle_reveal.to_program()
            last_sol = last_spend.solution.to_program()
            conds = driver.run_singleton(last_puz, last_sol)
            for c in conds:
                if c[0] == 51:
                    print(c[0], c[1].hex(), c[2])
            

        last_puz = last_spend.puzzle_reveal.to_program()
        last_sol = last_spend.solution.to_program()

        conds = driver.run_singleton(last_puz, last_sol)


        payment_coin, payment_coin_puzzle = await manager.choose_std_coin(nft.price())
        addr = await manager.wallet_client.get_next_address(1, False)
        ph = decode_puzzle_hash(addr)
        
        new_state = [100, 20202]
        new_state += [ph, manager.nft_pk]












    if func == "test":
        nft_id = hexstr_to_bytes("11775d04be2f67da5a531f87e83adf40392fa15b70b8b734a66c278adeae86e5")
        launcher_id = nft_id
        new_state = [100, 12020]
        await manager.nft_wallet.update_to_current_block()
        
        nft = await manager.nft_wallet.get_nft_by_launcher_id(nft_id)
        coin_rec = await manager.node_client.get_coin_record_by_name(nft.as_coin().name())
        print(coin_rec)
        
        addr = await manager.wallet_client.get_next_address(1, False)
        ph = decode_puzzle_hash(addr)
        new_state += [ph, manager.nft_pk]
        payment_coin, payment_coin_puzzle = await manager.choose_std_coin(nft.price())
        nft_spend, p2_spend, payment_spend = driver.make_buy_spend(nft, new_state, payment_coin, payment_coin_puzzle)

        # sb = await sign_coin_spends([nft_spend],
        #                             manager.pk_to_sk,
        #                             manager.AGG_SIG_ME_DATA,
        #                             DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,)

                        
                

    await manager.close()
    
    return manager


if __name__ == "__main__":

    func = sys.argv[1]
    
    m = asyncio.run(main(func))
