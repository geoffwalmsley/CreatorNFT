



class NFTCreator:
    def __init__(self, wallet_id, amount):
        self.wallet_id = wallet_id
        self.amount = amount
        assert self.amount % 2 = 1
        

    def load_ascii(self, filename):
        with open(filename, 'r') as f:
            lines = f.readlines()
        self.data = "".join(lines)

    def make_key_value(self):
        self.key_value
