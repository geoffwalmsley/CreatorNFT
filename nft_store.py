import asyncio
import aiosqlite
from pathlib import Path
from chia.util.db_wrapper import DBWrapper
from chia.full_node.coin_store import CoinStore



class NFTStore:

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
            db_name = "nft_wallet.db"
        db_filename = Path(db_name)
        self.connection = await aiosqlite.connect(db_filename)
        self.db_wrapper = DBWrapper(self.connection)
        self.coin_store = await CoinStore.create(self.db_wrapper)
        

    async def disconnect(self):
        if self.node_client:
            self.node_client.close()
            await self.node_client.await_close()

        if self.wallet_client:
            self.wallet_client.close()
            await self.wallet_client.await_close()

        if self.connection:
            self.connection.close()
    

if __name__ == "__main__":
    p = asyncio.run(main())
