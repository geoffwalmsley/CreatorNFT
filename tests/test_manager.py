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
from chia.util.bech32m import encode_puzzle_hash
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

            
    @pytest.mark.asyncio
    async def test_three(self, three_wallet_nodes):
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
        await server_1.start_client(PeerInfo(self_hostname, uint16(server_2._port)))
        await server_2.start_client(PeerInfo(self_hostname, uint16(server_0._port)))

        
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

            bs = await node_client_1.get_blockchain_state()
            print(bs)
            assert bs['peak'].height > 0

            amount = 101
            nft_data = ("CreatorNFT", "some data")
            launch_state = [100, 1000] # append ph and pk later
            royalty = [10]
            manager = NFTManager(wallet_client_0, node_client_0, "nft_store_test.db")
            await manager.connect()
            tx_id, launcher_id = await manager.launch_nft(amount, nft_data, launch_state, royalty)
            await manager.close()
            
        finally:
            await asyncio.sleep(5) # give the ongoing loops a second to finish.
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
            

            
        
