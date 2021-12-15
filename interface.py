import urwid
import asyncio

from nft_manager import NFTManager

palette = [
  ('titlebar', 'dark red', ''),
  ('refresh button', 'dark green,bold', ''),
  ('quit button', 'dark red', ''),
  ('getting quote', 'dark blue', ''),
  ('headers', 'white,bold', ''),
  ('good', 'dark green,bold', ''),
  ('bad', 'dark red,bold', '')]

menu = urwid.Text([
    u'Press (', ('refresh button', u'R'), u') to manually refresh. ',
    u'Press (', ('quit button', u'Q'), u') to quit.'
])

version = u'CreatorNFT v0.0.1'
filename = "bird1.txt"

# See this page about structuring urwid to work with asyncio
# https://seriot.ch/urwid/#5_async_async_requests.py


class Interface:
        
    async def update_node_status(self):
        
        # r = await self.aloop.run_in_executor(None, self.m, {"key":123})
        data = await self.nft_man.load_master_sk()
        data = await self.nft_man.nft_wallet.get_nft_state()
        if data['sync']['synced']:
            self.node_state = [("good", "Synced: ")]
        elif data['sync']['sync_mode']:
            self.node_state = [("bad", "Syncing: ")]
        else:
            self.node_state = [("bad", "Not synced: ")]
        self.node_state.append(("good", f"{data['peak'].height}"))
        header_text = [f" {version}\n "] +  self.node_state
        self.header_text.set_text(header_text)
        balances = await self.nft_man.wallet_client.get_wallet_balance(1) # wallet_id
        self.wallet_text.set_text(str(balances))

    async def update_balance(self):
        data = await self.nft_man.wallet_client.get_balances()
        self.quote_text.set_text(str(data))

    
    async def load_nft_manager(self):
        self.nft_man = NFTManager()
        await self.nft_man.connect()

    async def shutdown(self):
        await self.nft_man.disconnect()
        raise urwid.ExitMainLoop()

    def handle_key(self, key):
        if key in ('q',):
            self.aloop.create_task(self.shutdown())
        if key in ('n', 'N'):
            self.create_nft_page()


    def load_nft_file(self, filename):
        with open(filename, 'r') as f:
            nft_text = f.readlines()
        return nft_text
            

    def refresh(self, _loop, _data):
        _loop.draw_screen()
        self.aloop.create_task(self.update_node_status())
        _loop.set_alarm_in(10, self.refresh)
        

    def wrap_page_elements(self, elements):
        els = []
        for element in elements:
            content = urwid.Padding(urwid.Filler(element,
                                                 valign='top',
                                                 top=1,
                                                 bottom=1),
                                    align='center')
            if element != elements[0]:
                els.append(urwid.Filler(urwid.Divider()))
            els.append(content)
        return urwid.LineBox(urwid.Pile(els))
    
        
            
    def main_page(self):
        self.node_state = ""
        self.header_text = urwid.Text(f' CreatorNFT v0.0.1 {self.node_state}' )
        self.header = urwid.AttrMap(self.header_text, 'titlebar')
        
        self.wallet_text = urwid.Text(u'')
        self.mempool_text = urwid.Text(u'Mempool')
        self.nft_text = urwid.Text(self.load_nft_file('bird1.txt'))
        
        body = self.wrap_page_elements([self.wallet_text, self.mempool_text, self.nft_text])

        return urwid.Frame(header=self.header, body=body, footer=menu)
    

    def mint_nft(self, button, nft_data):
        message = urwid.Text(f"Minting NFT:\n{nft_data}")
        self.log.widget_list.append(message)
        p = "ok"


    async def update_mempool(self):
        data = await self.nft_man.node_client.get_all_mempool_items()
        if data:
            self.mempool_text.set_text(u'No Transactions in Mempool')
        self.mempool_text.set_text(str(data))
        return True

    async def mempool_listener(self):
        while True:
            _data = await self.nft_man.node_client.get_all_mempool_items()
            if _data:
                for item in _data:
                    w = urwid.Text(str(item))
                    self.log.widget_list.append(w)
                    
            await asyncio.sleep(1)


    def create_nft_page(self):
        nft_data = {'amount': 101,
                    'image_path': 'bird1.txt',
                    'price': 1000,
                    'royalty': 20}
        self.log = urwid.Pile([urwid.Text("")])
        title = urwid.Text(('headers', ' Amount (mojo): 101'))
        
        button = urwid.Button('Mint')
        urwid.connect_signal(button, 'click', self.mint_nft, nft_data)
        self.frame.set_body(self.wrap_page_elements([button, self.log]))
        
    
    

    def __init__(self):
        
        self.frame = self.main_page()
        
        self.aloop = asyncio.get_event_loop()
        
        self.aloop.create_task(self.load_nft_manager())
        self.aloop.create_task(self.mempool_listener())
        
        ev_loop = urwid.AsyncioEventLoop(loop=self.aloop)
        
        u_loop = urwid.MainLoop(self.frame,
                            palette=palette,
                            unhandled_input=self.handle_key,
                            event_loop=ev_loop)
        u_loop.set_alarm_in(0, self.refresh)
        u_loop.run()






app = Interface()
