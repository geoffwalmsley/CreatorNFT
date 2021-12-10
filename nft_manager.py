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
from chia.wallet.derive_keys import master_sk_to_wallet_sk, master_sk_to_singleton_owner_sk, master_sk_to_wallet_sk_unhardened
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
    def __init__(self, netname="testnet10"):
        self.netname = netname
        self.key_dict = {}
        

    async def connect(self, db_name=None):
        config = load_config(Path(DEFAULT_ROOT_PATH), 'config.yaml')
        self.prefix = "txch"
        if self.netname == "mainnet":
            self.prefix = "xch"
        
        self.AGG_SIG_ME_DATA = bytes.fromhex(config['farmer']['network_overrides']\
                                             ['constants'][self.netname]\
                                             ['AGG_SIG_ME_ADDITIONAL_DATA'])
        rpc_host = config["self_hostname"]
        full_node_rpc_port = config["full_node"]["rpc_port"]
        wallet_rpc_port = config["wallet"]["rpc_port"]
        self.node_client = await FullNodeRpcClient.create(
            rpc_host, uint16(full_node_rpc_port), Path(DEFAULT_ROOT_PATH), config
        )
        self.wallet_client = await WalletRpcClient.create(
            rpc_host, uint16(wallet_rpc_port), Path(DEFAULT_ROOT_PATH), config
        )
        if not db_name:
            db_name = "nft_store.db"
        db_filename = Path(db_name)
        self.connection = await aiosqlite.connect(db_filename)
        self.db_wrapper = DBWrapper(self.connection)
        self.nft_wallet = await NFTWallet.create(self.db_wrapper, self.node_client)
        await self.load_master_sk()
        await self.derive_nft_keys()
        await self.derive_wallet_keys()
        await self.derive_unhardened_keys()
        

    async def close(self):
        if self.node_client:
            self.node_client.close()

        if self.wallet_client:
            self.wallet_client.close()

        if self.connection:
            await self.connection.close()
            

    async def load_master_sk(self, fp_index=0):
        self.fingerprints = await self.wallet_client.get_public_keys()
        fp = self.fingerprints[fp_index]
        private_key = await self.wallet_client.get_private_key(fp)
        sk_data = binascii.unhexlify(private_key['sk'])
        self.master_sk = PrivateKey.from_bytes(sk_data)


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
            _sk = master_sk_to_wallet_sk_unhardened(self.master_sk, i)
            synth_sk = calculate_synthetic_secret_key(_sk, DEFAULT_HIDDEN_PUZZLE_HASH)
            self.key_dict[bytes(_sk.get_g1())] = _sk
            self.key_dict[bytes(synth_sk.get_g1())] = synth_sk


    async def pk_to_sk(self, pk):
        return self.key_dict.get(bytes(pk))


    async def choose_std_coin(self, amount):
        # addr = await self.wallet_client.get_next_address(1, False)
        # ph = decode_puzzle_hash(addr)
        # crs = await self.node_client.get_coin_records_by_puzzle_hash(ph, include_spent_coins=False)
        # keep amount low so we don't have to bother combining coins.
        if amount > 1e10:
            raise ValueError("Amount too high, choose a lower amount")

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
        launch_state += [puzzle_for_pk(self.nft_pk).get_tree_hash(), self.nft_pk]
        royalty.insert(0, puzzle_for_pk(self.nft_pk).get_tree_hash())
        
        found_coin, found_coin_puzzle = await self.choose_std_coin(amount)
        
        launcher_coin = Coin(found_coin.name(), LAUNCHER_PUZZLE_HASH, amount)

        launcher_spend = driver.make_launcher_spend(found_coin, amount, launch_state, royalty, nft_data)
        found_spend = driver.make_found_spend(found_coin, found_coin_puzzle, launcher_spend, amount)
        eve_spend = driver.make_eve_spend(launch_state, royalty, launcher_spend)


        sb = await sign_coin_spends(
            [launcher_spend, found_spend, eve_spend],
            self.pk_to_sk,
            self.AGG_SIG_ME_DATA,
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
                print("waiting")
                await asyncio.sleep(30)

    async def get_my_nfts(self):
        return await self.nft_wallet.get_nfts(self.nft_pk)

    async def update_nft(self, nft: NFT, new_state: List):
        update_spend = driver.make_update_spend(nft, new_state)
        sb = await sign_coin_spends([update_spend],
                                    self.pk_to_sk,
                                    self.AGG_SIG_ME_DATA,
                                    DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,)
        res = await self.node_client.push_tx(sb)
        if res['success']:
            print("txn added to mempool")
            tx_id = await self.get_tx_from_mempool(sb.name())
            return tx_id

    async def get_for_sale_nfts(self):
        launcher_ids = await self.nft_wallet.get_nfts()
        for_sale_nfts = []
        for launcher_id in launcher_ids[:10]:
            nft = await self.nft_wallet.get_nft_by_launcher_id(launcher_id)
            if int_from_bytes(nft.state()[0]) == 100:
                if not await self.is_my_nft(nft):
                    for_sale_nfts.append(nft)
        return for_sale_nfts


    async def is_my_nft(self, nft: NFT):
        if nft.owner_pk() == bytes(self.nft_pk):
            return True

    async def buy_nft(self, nft: NFT):
        addr = await self.wallet_client.get_next_address(1, True)
        ph = decode_puzzle_hash(addr)
        new_state = [90, nft.price(), ph, self.nft_pk]
        payment_coin, payment_coin_puzzle = await self.choose_std_coin(nft.price())
        nft_spend, p2_spend, payment_spend = driver.make_buy_spend(nft, new_state, payment_coin, payment_coin_puzzle)
    
        sb = await sign_coin_spends([nft_spend, p2_spend, payment_spend],
                                    self.pk_to_sk,
                                    self.AGG_SIG_ME_DATA,
                                    DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,)
        res = await self.node_client.push_tx(sb)
        if res['success']:
            print("txn added to mempool")
            tx_id = await self.get_tx_from_mempool(sb.name())
            return tx_id

        
        
async def main():
    # DATA
    amount = 101
    nft_data = ("NFT TEST", "Hash Data")
    launch_state = [100, 1000] # append ph and pk later
    royalty = [10]

    
    manager = NFTManager()
    await manager.connect()

    # Update to current block
    await manager.nft_wallet.update_to_current_block()

    # Launch a new NFT
    # tx_id, launcher_id = await manager.launch_nft(amount, nft_data, launch_state, royalty)
    # print(f"\nSubmitted tx: {tx_id}")
    # nft = await manager.wait_for_confirmation(tx_id, launcher_id)
    # asyncio.sleep(2)
    
    # List stored NFTs
    # await manager.nft_wallet.update_to_current_block()
    # nfts = await manager.get_my_nfts()
    # print(nfts)    
    
    # State update
    # my_nft = await manager.nft_wallet.get_nft_by_launcher_id(nfts[-1])
    # new_state = [12720, 1000, puzzle_for_pk(manager.nft_pk).get_tree_hash(), manager.nft_pk]
    # tx_id = await manager.update_nft(my_nft, new_state)
    # nft = await manager.wait_for_confirmation(tx_id, my_nft.launcher_id)
    # print(nft.state())

    # Find for sale nfts    
    nfts_for_sale = await manager.get_for_sale_nfts()

    # Purchase spend (needs second wallet)
    if nfts_for_sale:
        nft = nfts_for_sale[-1]
        print(nft)

    # tx_id = await manager.buy_nft(nft)
    # print(tx_id)
    

    await manager.close()
    
    return manager


if __name__ == "__main__":
    m = asyncio.run(main())
