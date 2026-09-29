"""帮派商店与贡献兑换系统"""
import threading
import time
from dataclasses import dataclass, field

DAY_SECONDS = 86400
WEEK_SECONDS = 7 * DAY_SECONDS

# 帮派等级折扣：每级 2%，上限 30%
GUILD_LEVEL_DISCOUNT_PER_LEVEL = 0.02
GUILD_LEVEL_DISCOUNT_CAP = 0.30
# 会员等级折扣：每级 1%，上限 15%
MEMBER_LEVEL_DISCOUNT_PER_LEVEL = 0.01
MEMBER_LEVEL_DISCOUNT_CAP = 0.15


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
    # personal_limit 的统计周期: "day" / "week" / "total"
    limit_period: str = "week"
    # 轮换刷新时补到的目标库存
    restock_to: int = 0
    # 下架标记（过期自动置 True）
    delisted: bool = False


@dataclass
class ContributionEntry:
    pid: str
    amount: int
    source: str
    timestamp: float
    operator: str


class GuildShop:
    def __init__(self, rotation_size: int = 4):
        self.items: dict[str, ShopItem] = {}
        # bug1 修复: 所有贡献变动记录日志
        self.contribution_log: list[ContributionEntry] = []
        self.contribution: dict[str, int] = {}
        self.next_refresh: float = 0
        # (pid, iid, period_key) -> count
        self.personal_purchased: dict = {}
        # iid -> count（全帮总量限购）
        self.guild_purchased: dict = {}
        # bug2 修复: 轮换池 + 个人刷新券
        self.rotation_pool: list[str] = []
        self.rotation_size = rotation_size
        self._rotation_cursor = 0
        self.refresh_tickets: dict[str, int] = {}
        # bug7 修复: 库存锁，保证检查+扣除原子
        self._lock = threading.Lock()
        # bug8 修复: 解散归档
        self.archive: dict[str, dict] = {}

    # ---------- 贡献 ----------
    def add_contribution(self, pid: str, amount: int, source: str,
                         operator: str = "", timestamp: float = 0):
        """记录贡献变动日志（来源/数量/时间/操作人）。"""
        if amount == 0:
            return
        ts = timestamp or time.time()
        op = operator or pid
        with self._lock:
            self.contribution[pid] = self.contribution.get(pid, 0) + amount
            self.contribution_log.append(ContributionEntry(
                pid=pid, amount=amount, source=source,
                timestamp=ts, operator=op,
            ))

    def transfer_contribution(self, from_pid: str, to_pid: str, amount: int) -> bool:
        """bug5 修复: 贡献不可转让，只能通过帮派活动获取。"""
        return False

    # ---------- 刷新（轮换 + 刷新券） ----------
    def refresh_shop(self, current_time: float):
        """轮换刷新：每次只轮换一部分商品并补货，不再全量重置。"""
        if not self.rotation_pool:
            self.rotation_pool = list(self.items)
        if not self.rotation_pool:
            self.next_refresh = current_time
            return
        rotated = []
        for _ in range(min(self.rotation_size, len(self.rotation_pool))):
            iid = self.rotation_pool[self._rotation_cursor % len(self.rotation_pool)]
            self._rotation_cursor += 1
            rotated.append(iid)
        for iid in rotated:
            item = self.items.get(iid)
            if item is None:
                continue
            target = item.restock_to or item.stock
            if item.stock < target:
                item.stock = target
        self.next_refresh = current_time

    def give_refresh_ticket(self, pid: str, count: int = 1):
        self.refresh_tickets[pid] = self.refresh_tickets.get(pid, 0) + count

    def personal_refresh(self, pid: str, current_time: float) -> bool:
        """个人刷新券：消耗一张券立即触发一次轮换，避免蹲点抢货。"""
        with self._lock:
            if self.refresh_tickets.get(pid, 0) <= 0:
                return False
            self.refresh_tickets[pid] -= 1
        self.refresh_shop(current_time)
        return True

    # ---------- 价格 ----------
    def get_price(self, iid: str, guild_level: int,
                  tech_discount: float = 0.0, member_level: int = 1) -> int:
        """bug4 修复: 价格随帮派等级、科技折扣、会员等级优惠计算。"""
        base = self.items[iid].price
        guild_disc = min(max(guild_level - 1, 0) * GUILD_LEVEL_DISCOUNT_PER_LEVEL,
                         GUILD_LEVEL_DISCOUNT_CAP)
        member_disc = min(max(member_level - 1, 0) * MEMBER_LEVEL_DISCOUNT_PER_LEVEL,
                          MEMBER_LEVEL_DISCOUNT_CAP)
        tech_disc = min(max(tech_discount, 0.0), 0.5)
        total_disc = min(guild_disc + member_disc + tech_disc, 0.9)
        return max(1, int(base * (1 - total_disc)))

    # ---------- 可用性 ----------
    def is_item_available(self, iid: str, current_time: float) -> bool:
        """bug6 修复: 过期商品自动下架且不可兑换。"""
        item = self.items.get(iid)
        if item is None:
            return False
        if item.delisted:
            return False
        if item.expires_at and current_time >= item.expires_at:
            item.delisted = True  # 自动下架
            return False
        return True

    # ---------- 限购 ----------
    def _period_key(self, item: ShopItem, current_time: float) -> str:
        if item.limit_period == "day":
            return f"d{int(current_time // DAY_SECONDS)}"
        if item.limit_period == "week":
            return f"w{int(current_time // WEEK_SECONDS)}"
        return "total"

    def _check_limits(self, pid: str, item: ShopItem, qty: int,
                      current_time: float) -> str:
        key = (pid, item.iid, self._period_key(item, current_time))
        if self.personal_purchased.get(key, 0) + qty > item.personal_limit:
            return "personal limit exceeded"
        if self.guild_purchased.get(item.iid, 0) + qty > item.guild_limit:
            return "guild limit exceeded"
        return ""

    # ---------- 兑换 ----------
    def purchase(self, pid: str, iid: str, qty: int,
                 current_time: float = 0) -> tuple[bool, str]:
        """bug3/6/7 修复: 限购检查 + 过期检查 + 原子库存扣减。"""
        if qty <= 0:
            return False, "invalid qty"
        with self._lock:
            item = self.items.get(iid)
            if item is None:
                return False, "no such item"
            now = current_time or time.time()
            if not self.is_item_available(iid, now):
                return False, "item unavailable"
            err = self._check_limits(pid, item, qty, now)
            if err:
                return False, err
            if item.stock < qty:
                return False, "no stock"
            # 锁内完成检查与扣减，并发时只有一个成功
            item.stock -= qty
            key = (pid, iid, self._period_key(item, now))
            self.personal_purchased[key] = self.personal_purchased.get(key, 0) + qty
            self.guild_purchased[iid] = self.guild_purchased.get(iid, 0) + qty
            return True, "ok"

    # ---------- 解散归档 ----------
    def archive_on_disband(self, guild_id: str):
        """bug8 修复: 解散时归档商店数据，重建后是全新空商店。"""
        self.archive[guild_id] = {
            "items": dict(self.items),
            "contribution_log": list(self.contribution_log),
            "guild_purchased": dict(self.guild_purchased),
            "archived_at": time.time(),
        }
        self.items.clear()
        self.contribution_log.clear()
        self.contribution.clear()
        self.personal_purchased.clear()
        self.guild_purchased.clear()
        self.rotation_pool = []
        self._rotation_cursor = 0
