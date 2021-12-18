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

  ```sh
  git clone https://github.com/geoffwalmsley/CreatorNFT.git
  pip install --editable .
  ```

### Usage

   ```sh
   nft launch -d art/bird1.txt -r 10 -p 1200 -a 101
   ```

   ```sh
   nft list
   ```
