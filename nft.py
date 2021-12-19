import click
import asyncio
from functools import wraps
from pathlib import Path

from chia.util.byte_types import hexstr_to_bytes

from nft_manager import NFTManager
from nft_wallet import NFT

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def coro(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


def print_nft(nft: NFT):
    print("\n")
    print("-" * 64)
    print(f"NFT ID:\n{nft.launcher_id.hex()}\n")
    print(f"Owner: {nft.owner_fingerprint()}")
    if nft.is_for_sale():
        print("Status: For Sale")
    else:
        print("Status: Not for sale")
    print(f"Price: {nft.price()}")
    print(f"Royalty: {nft.royalty_pc()}%\n")
    print(f"Chialisp: {str(nft.data[0])}\n")
    print(f"Data:\n")
    print(nft.data[1].decode("utf-8"))
    print("-" * 64)
    print("\n")


@click.group(
    help=f"\n  CreatorNFT v0.1\n",
    epilog="Try 'nft list' or 'nft sale' to see some NFTs",
    context_settings=CONTEXT_SETTINGS,
)
@click.pass_context
def cli(ctx: click.Context):
    ctx.ensure_object(dict)


@cli.command("init", short_help="Start the nft database")
@coro
async def init_cmd():
    manager = NFTManager()
    await manager.connect()
    await manager.sync()
    await manager.close()


@cli.command("view", short_help="View a single NFT by id")
@click.option("-n", "--nft-id", required=True, type=str)
@click.pass_context
@coro
async def view_cmd(ctx, nft_id):
    manager = NFTManager()
    await manager.connect()
    nft = await manager.view_nft(hexstr_to_bytes(nft_id))
    if nft:
        print_nft(nft)
    else:
        print(f"\nNo record found for:\n{nft_id}")
    await manager.close()


@cli.command("list", short_help="Show CreatorNFT version")
@click.pass_context
@coro
async def list_cmd(ctx) -> None:
    manager = NFTManager()
    await manager.connect()
    nfts = await manager.get_my_nfts()
    await manager.close()
    for nft in nfts:
        print_nft(nft)


@cli.command("list-for-sale", short_help="Show some NFTs for sale")
@click.pass_context
@coro
async def sale_cmd(ctx) -> None:
    manager = NFTManager()
    await manager.connect()
    nfts = await manager.get_for_sale_nfts()
    for nft in nfts:
        print_nft(nft)
    await manager.close()


@cli.command("launch", short_help="Launch a new NFT")
@click.option("-d", "--data", required=True, type=str)
@click.option("-r", "--royalty", required=True, type=int)
@click.option("-a", "--amount", type=int, default=101)
@click.option("-p", "--price", type=int, default=1000)
@click.option("--for-sale/--not-for-sale", type=bool, default=False)
@click.pass_context
@coro
async def launch_cmd(ctx, data, royalty, amount, price, for_sale) -> None:
    assert price > 1000
    assert amount % 2 == 1
    price = round(price, -3)

    manager = NFTManager()
    await manager.connect()
    with open(Path(data), "r") as f:
        datastr = f.readlines()
    nft_data = ("CreatorNFT", "".join(datastr))
    if for_sale:
        launch_state = [10, price]
    else:
        launch_state = [0, price]
    royalty = [royalty]
    tx_id, launcher_id = await manager.launch_nft(amount, nft_data, launch_state, royalty)
    print(f"Transaction id: {tx_id}")
    nft = await manager.wait_for_confirmation(tx_id, launcher_id)
    print("\n\n NFT Launched!!")
    print_nft(nft)
    await manager.close()


@cli.command("update", short_help="Update one of your NFTs")
@click.option("-n", "--nft-id", required=True, type=str)
@click.option("-p", "--price", required=True, type=int)
@click.option("--for-sale/--not-for-sale", required=True, type=bool, default=False)
@click.pass_context
@coro
async def update_cmd(ctx, nft_id, price, for_sale):
    assert price > 1000
    price = round(price, -3)
    manager = NFTManager()
    await manager.connect()
    if for_sale:
        new_state = [10, price]
    else:
        new_state = [0, price]
    tx_id = await manager.update_nft(hexstr_to_bytes(nft_id), new_state)
    print(f"Transaction id: {tx_id}")
    nft = await manager.wait_for_confirmation(tx_id, hexstr_to_bytes(nft_id))
    print("\n\n NFT Updated!!")
    print_nft(nft)
    await manager.close()


@cli.command("buy", short_help="Update one of your NFTs")
@click.option("-n", "--nft-id", required=True, type=str)
@click.option("-p", "--price", required=True, type=int)
@click.option("--for-sale/--not-for-sale", required=True, type=bool, default=False)
@click.pass_context
@coro
async def buy_cmd(ctx, nft_id, price, for_sale):
    assert price > 1000
    price = round(price, -3)
    manager = NFTManager()
    await manager.connect()
    if for_sale:
        new_state = [10, price]
    else:
        new_state = [0, price]
    tx_id = await manager.buy_nft(hexstr_to_bytes(nft_id), new_state)
    print(f"Transaction id: {tx_id}")
    nft = await manager.wait_for_confirmation(tx_id, hexstr_to_bytes(nft_id))
    print("\n\n NFT Purchased!!")
    print_nft(nft)
    await manager.close()


def monkey_patch_click() -> None:
    import click.core

    click.core._verify_python3_env = lambda *args, **kwargs: 0  # type: ignore[attr-defined]


def main() -> None:
    monkey_patch_click()
    asyncio.run(cli())


if __name__ == "__main__":
    main()
