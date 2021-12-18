import asyncio

from operator import attrgetter
from chia.util.config import load_config, save_config
import logging
from pathlib import Path

import pytest

from chia.consensus.block_rewards import calculate_base_farmer_reward, calculate_pool_reward
from chia.rpc.full_node_rpc_api import FullNodeRpcApi
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.rpc_server import start_rpc_server
from chia.rpc.wallet_rpc_api import WalletRpcApi
from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.simulator.simulator_protocol import FarmNewBlockProtocol
from chia.types.peer_info import PeerInfo
from chia.util.bech32m import encode_puzzle_hash, decode_puzzle_hash
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.consensus.coinbase import create_puzzlehash_for_pk
from chia.wallet.derive_keys import master_sk_to_wallet_sk
from chia.util.ints import uint16, uint32
from chia.wallet.transaction_record import TransactionRecord
from chia.protocols.full_node_protocol import RespondBlock
# from chia.wallet.transaction_sorting import SortKey
from tests.setup_nodes import bt, setup_simulators_and_wallets, self_hostname
from tests.time_out_assert import time_out_assert
from tests.util.rpc import validate_get_routes
from tests.connection_utils import connect_and_get_peer
from nft_manager import NFTManager


class TestNFTWallet:
    @pytest.fixture(scope="function")
    async def two_wallet_nodes(self):
        async for _ in setup_simulators_and_wallets(1, 2, {}):
            yield _

    @pytest.fixture(scope="function")
    async def three_wallet_nodes(self):
        async for _ in setup_simulators_and_wallets(3, 3, {}):
            yield _

            
    # @pytest.mark.asyncio
    @pytest.fixture(scope="function")
    async def three_nft_managers(self, three_wallet_nodes, tmp_path):
        num_blocks = 5
        config = bt.config
        hostname = config["self_hostname"]
        daemon_port = config["daemon_port"]
        wallet_rpc_port_0 = 21520
        wallet_rpc_port_1 = 21521
        wallet_rpc_port_2 = 21522

        node_rpc_port_0 = 21530
        node_rpc_port_1 = 21531
        node_rpc_port_2 = 21532
        
        full_nodes, wallets = three_wallet_nodes
        
        wallet_0, wallet_server_0 = wallets[0]
        wallet_1, wallet_server_1 = wallets[1]
        wallet_2, wallet_server_2 = wallets[2]
        
        full_node_api_0 = full_nodes[0]
        full_node_api_1 = full_nodes[1]
        full_node_api_2 = full_nodes[2]

        full_node_0 = full_node_api_0.full_node
        full_node_1 = full_node_api_1.full_node
        full_node_2 = full_node_api_2.full_node

        server_0 = full_node_0.server
        server_1 = full_node_1.server
        server_2 = full_node_2.server

        
        # wallet_0 <-> server_0
        await wallet_server_0.start_client(PeerInfo(self_hostname, uint16(server_0._port)), None)
        # wallet_1 <-> server_1
        await wallet_server_1.start_client(PeerInfo(self_hostname, uint16(server_1._port)), None)
        # wallet_2 <-> server_2
        await wallet_server_2.start_client(PeerInfo(self_hostname, uint16(server_2._port)), None)

        await server_0.start_client(PeerInfo(self_hostname, uint16(server_1._port)))
        await server_0.start_client(PeerInfo(self_hostname, uint16(server_2._port)))
        await server_1.start_client(PeerInfo(self_hostname, uint16(server_2._port)))
        await server_1.start_client(PeerInfo(self_hostname, uint16(server_0._port)))
        await server_2.start_client(PeerInfo(self_hostname, uint16(server_0._port)))
        await server_2.start_client(PeerInfo(self_hostname, uint16(server_1._port)))
        
        def stop_node_cb():
            pass
        
        wallet_rpc_api_0 = WalletRpcApi(wallet_0)
        wallet_rpc_api_1 = WalletRpcApi(wallet_1)
        wallet_rpc_api_2 = WalletRpcApi(wallet_2)
        
        full_node_rpc_api_0 = FullNodeRpcApi(full_node_0)
        full_node_rpc_api_1 = FullNodeRpcApi(full_node_1)
        full_node_rpc_api_2 = FullNodeRpcApi(full_node_2)
        
        rpc_cleanup_node_0 = await start_rpc_server(
            full_node_rpc_api_0,
            hostname,
            daemon_port,
            node_rpc_port_0,
            stop_node_cb,
            bt.root_path,
            config,
            connect_to_daemon=False,
        )
        rpc_cleanup_node_1 = await start_rpc_server(
            full_node_rpc_api_1,
            hostname,
            daemon_port,
            node_rpc_port_1,
            stop_node_cb,
            bt.root_path,
            config,
            connect_to_daemon=False,
        )
        rpc_cleanup_node_2 = await start_rpc_server(
            full_node_rpc_api_2,
            hostname,
            daemon_port,
            node_rpc_port_2,
            stop_node_cb,
            bt.root_path,
            config,
            connect_to_daemon=False,
        )
        
        rpc_cleanup_wallet_0 = await start_rpc_server(
            wallet_rpc_api_0,
            hostname,
            daemon_port,
            wallet_rpc_port_0,
            stop_node_cb,
            bt.root_path,
            config,
            connect_to_daemon=False,
        )
        rpc_cleanup_wallet_1 = await start_rpc_server(
            wallet_rpc_api_1,
            hostname,
            daemon_port,
            wallet_rpc_port_1,
            stop_node_cb,
            bt.root_path,
            config,
            connect_to_daemon=False,
        )
        rpc_cleanup_wallet_2 = await start_rpc_server(
            wallet_rpc_api_2,
            hostname,
            daemon_port,
            wallet_rpc_port_2,
            stop_node_cb,
            bt.root_path,
            config,
            connect_to_daemon=False,
        )

        wallet_client_0 = await WalletRpcClient.create(self_hostname, wallet_rpc_port_0, bt.root_path, config)
        wallet_client_1 = await WalletRpcClient.create(self_hostname, wallet_rpc_port_1, bt.root_path, config)
        wallet_client_2 = await WalletRpcClient.create(self_hostname, wallet_rpc_port_2, bt.root_path, config)

        node_client_0 = await FullNodeRpcClient.create(self_hostname, node_rpc_port_0, bt.root_path, config)
        node_client_1 = await FullNodeRpcClient.create(self_hostname, node_rpc_port_1, bt.root_path, config)
        node_client_2 = await FullNodeRpcClient.create(self_hostname, node_rpc_port_2, bt.root_path, config)

        
        try:
            # Setup Initial Balances
            # Wallet_0 has coinbase only
            # Wallet_1 has coinbase and received
            # Wallet_2 has received only
            ph_0 = await wallet_0.wallet_state_manager.main_wallet.get_new_puzzlehash()
            ph_1 = await wallet_1.wallet_state_manager.main_wallet.get_new_puzzlehash()
            ph_2 = await wallet_2.wallet_state_manager.main_wallet.get_new_puzzlehash()

            for i in range(0, num_blocks):
                await full_node_api_0.farm_new_transaction_block(FarmNewBlockProtocol(ph_0))

            assert await wallet_0.wallet_state_manager.main_wallet.get_confirmed_balance() > 0
            # assert await wallet_0.wallet_state_manager.main_wallet.get_confirmed_balance() > 0

            manager_0 = NFTManager(wallet_client_0, node_client_0, tmp_path/"nft_store_test_0.db")
            manager_1 = NFTManager(wallet_client_1, node_client_1, tmp_path/"nft_store_test_1.db")
            manager_2 = NFTManager(wallet_client_2, node_client_2, tmp_path/"nft_store_test_2.db")


            yield (manager_0, manager_1, manager_2, full_node_api_0, full_node_api_1, full_node_api_2)


            await manager_0.close()
            await manager_1.close()
            await manager_2.close()
            
        finally:
            await asyncio.sleep(2) # give the ongoing loops a second to finish.
            await rpc_cleanup_node_0()
            await rpc_cleanup_node_1()
            await rpc_cleanup_node_2()
            await rpc_cleanup_wallet_0()
            await rpc_cleanup_wallet_1()
            await rpc_cleanup_wallet_2()
            wallet_client_0.close()
            wallet_client_1.close()
            wallet_client_2.close()
            node_client_0.close()
            node_client_1.close()
            node_client_2.close()
            await wallet_client_0.await_closed()
            await wallet_client_1.await_closed()
            await wallet_client_2.await_closed()
            await node_client_0.await_closed()
            await node_client_1.await_closed()
            await node_client_2.await_closed()

            
    @pytest.mark.asyncio
    async def test_launch_and_find_on_other_nodes(self, three_nft_managers):
        man_0, man_1, man_2, full_node_api_0, full_node_api_1, full_node_api_2 = three_nft_managers
        await man_0.connect()
        await man_1.connect()
        await man_2.connect()
        amount = 101
        nft_data = ("CreatorNFT", "some data")
        for_sale_launch_state = [100, 1000] 
        not_for_sale_launch_state = [90, 1000] 
        royalty = [10]
        tx_id, launcher_id = await man_0.launch_nft(amount, nft_data, for_sale_launch_state, royalty)
        assert tx_id
        for i in range(0, 5):
            await full_node_api_0.farm_new_transaction_block(FarmNewBlockProtocol(bytes32(b"a" * 32)))
        # Check other managers find for_sale_nfts
        coins_for_sale_1 = await man_1.get_for_sale_nfts()
        coins_for_sale_2 = await man_2.get_for_sale_nfts()
        assert coins_for_sale_1[0].launcher_id == launcher_id
        assert coins_for_sale_2[0].launcher_id == launcher_id


    @pytest.mark.asyncio
    async def test_coin_selection(self, three_nft_managers):
        man_0, man_1, man_2, full_node_api_0, full_node_api_1, full_node_api_2 = three_nft_managers
        await man_0.connect()
        await man_1.connect()
        await man_2.connect()
        amount = 100
        man_0_balance = await man_0.available_balance()
        assert man_0_balance > 0
        balance = await man_1.available_balance()
        assert balance == 0
        balance = await man_2.available_balance()
        assert balance == 0

        # send 1xch from man_0 to man_1
        man_1_addr = await man_1.wallet_client.get_next_address(1, False)
        man_0_addr = await man_0.wallet_client.get_next_address(1, False)

        tx = await man_0.wallet_client.send_transaction("1", int(1e12), man_1_addr)
        tx_id = tx.name

        async def tx_in_mempool():
                tx = await man_0.wallet_client.get_transaction("1", tx_id)
                return tx.is_in_mempool()

        await time_out_assert(5, tx_in_mempool, True)

        assert (await man_0.wallet_client.get_wallet_balance("1"))["unconfirmed_wallet_balance"] == man_0_balance - int(1e12)
  
        man_0_balance = (await man_0.wallet_client.get_wallet_balance("1"))["confirmed_wallet_balance"]
        print(man_0_balance)
        
        async def eventual_balance():
            return (await man_0.wallet_client.get_wallet_balance("1"))["confirmed_wallet_balance"]
        for i in range(0, 5):
            await full_node_api_0.farm_new_transaction_block(FarmNewBlockProtocol(decode_puzzle_hash(man_0_addr)))

        async def tx_confirmed():
            tx = await man_0.wallet_client.get_transaction("1", tx_id)
            return tx.confirmed

        await time_out_assert(10, tx_confirmed, True)
        txns = await man_1.wallet_client.get_transactions("1")
        print(txns)
        
        assert await man_1.available_balance() == int(1e12)

        
