"""帮派商店与贡献兑换系统 - 含8个bug"""
from dataclasses import dataclass, field

@dataclass
class ShopItem:
    iid: str
    name: str
    price: int
    stock: int
    personal_limit: int = 999
    guild_limit: int = 999
    expires_at: float = 0
    category: str = "normal"

class GuildShop:
    def __init__(self):
        self.items: dict[str, ShopItem] = {}
        self.contribution_log: list = []  # bug1: 无日志
        self.next_refresh: float = 0
        self.personal_purchased: dict = {}  # (pid, iid) -> count
        self.guild_purchased: dict = {}  # iid -> count

    def add_contribution(self, pid: str, amount: int, source: str):
        # bug1: 不记录日志
        pass

    def refresh_shop(self, current_time: float):
        # bug2: 全量刷新无轮换
        for item in self.items.values():
            item.stock = 100

    def purchase(self, pid: str, iid: str, qty: int) -> tuple[bool, str]:
        item = self.items[iid]
        # bug3: 不检查个人限购
        # bug4: 价格固定
        # bug6: 不过期
        # bug7: 非原子库存
        if item.stock >= qty:
            item.stock -= qty
            return True, "ok"
        return False, "no stock"

    def get_price(self, iid: str, guild_level: int) -> int:
        # bug4: 不随等级折扣
        return self.items[iid].price

    def transfer_contribution(self, from_pid: str, to_pid: str, amount: int) -> bool:
        # bug5: 可转让
        return True

    def is_item_available(self, iid: str, current_time: float) -> bool:
        # bug6: 不过期
        return iid in self.items

    def archive_on_disband(self, guild_id: str):
        # bug8: 不归档
        pass
