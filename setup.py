from setuptools import setup

setup(
    name="CreatorNFT",
    version="0.1",
    py_modules=["nft"],
    install_requires=["Click", "pytimeparse"],
    entry_points={"console_scripts": ["nft = nft:main"]},
)
