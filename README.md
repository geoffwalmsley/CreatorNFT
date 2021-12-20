## Chialisp NFT with Perpetual Creator Royalties

This is a chialisp NFT in which the creator/minter defines a puzzle hash which will capture a fixed percentage of the value each time the singleton is traded.

Coins locked with the NFT hold the usual key/value data as well as some simple state:
* For sale/Not for sale
* Price
* Owner Puzzlehash
* Owner Pubkey
* Royalty percentage (immutable)
* Creator puzzle hash (immutable)

If the puzzle is flagged as for sale, anyone can us the p2_singleton to buy the nft for the price set by the owner. When the transaction is made, the puzzle outputs conditions which pay the royalty percentage to the creator, the remainder to the owner, and recreates the singleton coin with the details of the new owner.

There is basic wallet functionality to identify coins marked as for-sale on the block chain, and keeping track of owned coins.


### Installation

To set up testnet10, best to follow the instructions for the CAT tutorial at chialisp.com. From there you can just use the venv you use for the protocol_and_cats_rebased branch.



  ```sh
  git clone https://github.com/geoffwalmsley/CreatorNFT.git
  pip install --editable .
  ```

Once that's done you can start the DB and sync the current NFTs with:

	```sh
	nft init
	```


### Usage


   ```sh
   # Launch a new NFT
   nft launch -d <path-to-data> -r 10 -p 1200 -a 101
   
   # List owned NFTs
   nft list
   
   # List for-sale NFTS
   nft list-for-sale
   
   # View a specific NFT
   nft view -n <NFT-ID>
   
   # Update an owned nft
   nft update -n <NFT-ID> -p price --for-sale
   
   # Buy NFT
   nft buy -n <NFT-ID>
   ```
