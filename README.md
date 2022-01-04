## Chialisp NFT with Perpetual Creator Royalties

This is a chialisp NFT in which the creator/minter defines a puzzle hash which will capture a fixed percentage of the value each time the singleton is traded.

There's also a video giving an overview of the functionality and a look at the chialisp, [here](https://drive.google.com/file/d/120Ky-LiDOOwsTEBtSmEKLBTChcrNUQda/view?usp=sharing) (Note the video quality is better if you download it... for some reason streaming it lowers the resolution making it unreadable)


Coins locked with the NFT hold the usual key/value data as well as some simple state:
* For sale/Not for sale
* Price
* Owner Puzzlehash
* Owner Pubkey
* Royalty percentage (immutable)
* Creator puzzle hash (immutable)

If the puzzle is flagged as for sale, anyone can buy the nft for the price set by the owner. When the transaction is made, the puzzle outputs conditions which pay the royalty percentage to the creator, the remainder to the owner, and recreates the nft with the details of the new owner.

There is basic wallet functionality to identify coins marked as for-sale on the block chain, and keeping track of owned coins. It isn't built to work with offer files, just intended to be a simple way to have something on chain that you can interact with a bit.

![Screenshot](screenshot.png)


## Mainnet Installation
To run the wallet on mainnet, you have to checkout the `setup_for_mainnet` branch. The key derivation for the wallet will work better once the chia `protocol_and_cats_rebased` branch is merged into chia main branch.

```
git clone https://github.com/geoffwalmsley/CreatorNFT.git
cd CreatorNFT/
git checkout setup_for_mainnet
pip install --editable .
```

To start the database run:

```
nft init
```

To see all the listed NFTs run:

```
nft list-for-sale
```



### Testnet Installation

To set up testnet10, best to follow the instructions for the CAT tutorial at chialisp.com. From there you can just use the venv you use for the protocol_and_cats_rebased branch.



  ```
  git clone https://github.com/geoffwalmsley/CreatorNFT.git
  cd CreatorNFT/
  pip install --editable .
  ```

Once that's done, make sure you're in the CreatorNFT directory, and you can start the DB and sync the current NFTs with:

	```
	nft init
	```


## Usage


   ```
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

## Testing

For testing make sure to remove references to master_sk_to_wallet_sk_unhardened as its only available in the protovol_and_cats_branch. Tests need main branch to run.


```
pytest tests/
```

### License
Copyright 2021 Geoff Walmsley

Licensed under the Apache License, Version 2.0 (the "License");
you may not use these files except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
