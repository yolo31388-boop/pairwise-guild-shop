"""帮派商店与贡献兑换系统"""
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ShopItem:
    iid: str
    name: str
    price: int
    stock: int
    personal_limit: int = 999   # 每人每日限购
    guild_limit: int = 999      # 全帮总量限购
    expires_at: float = 0       # 0 表示永不过期
    category: str = "normal"
    weekly_limit: int = 999     # 每人每周限购
    delisted: bool = False      # 过期/下架标记


class GuildShop:
    DAY_SECONDS = 86400
    WEEK_SECONDS = 7 * 86400
    REFRESH_INTERVAL = 4 * 3600
    RESTOCK_AMOUNT = 20
    MAX_GUILD_DISCOUNT = 0.30   # 帮派等级折扣上限 30%
    GUILD_DISCOUNT_PER_LEVEL = 0.02
    MEMBER_DISCOUNT_PER_LEVEL = 0.01
    MAX_MEMBER_DISCOUNT = 0.10

    def __init__(self, guild_level: int = 1, tech_discount: float = 0.0):
        self.items: dict[str, ShopItem] = {}
        self.contribution_log: list = []
        self.contributions: dict[str, int] = {}
        self.rotation_pool: list = []       # 轮换池：当前上架的 iid 窗口
        self.rotation_size = 3
        self.rotation_index = 0
        self.refresh_tickets: dict[str, int] = {}  # 个人刷新券
        self.next_refresh: float = 0
        self.personal_purchased: dict = {}  # (pid, iid) -> [(ts, qty)]
        self.guild_purchased: dict = {}     # iid -> count
        self.archive: dict = {}             # guild_id -> 归档快照
        self.guild_level = guild_level
        self.tech_discount = tech_discount
        self._lock = threading.Lock()

    # ---- bug1: 贡献变动全部记录日志（来源/数量/时间/操作人） ----
    def _log_contribution(self, pid: str, amount: int, source: str,
                          ts: float, operator: str):
        self.contribution_log.append({
            "pid": pid,
            "amount": amount,
            "source": source,
            "time": ts,
            "operator": operator,
        })

    def add_contribution(self, pid: str, amount: int, source: str,
                         ts: float = None, operator: str = None):
        ts = time.time() if ts is None else ts
        operator = pid if operator is None else operator
        with self._lock:
            self.contributions[pid] = self.contributions.get(pid, 0) + amount
            self._log_contribution(pid, amount, source, ts, operator)

    # ---- bug2: 轮换刷新 + 个人刷新券，不再固定时间全量重置 ----
    def refresh_shop(self, current_time: float):
        with self._lock:
            self._delist_expired(current_time)
            iids = list(self.items)
            if iids:
                # 轮换机制：每次只补货轮换窗口内的一批商品
                start = self.rotation_index % len(iids)
                window = [iids[(start + k) % len(iids)]
                          for k in range(min(self.rotation_size, len(iids)))]
                self.rotation_pool = window
                for iid in window:
                    item = self.items[iid]
                    if not item.delisted:
                        item.stock += self.RESTOCK_AMOUNT
                self.rotation_index += self.rotation_size
            self.next_refresh = current_time + self.REFRESH_INTERVAL

    def grant_refresh_tickets(self, pid: str, count: int):
        self.refresh_tickets[pid] = self.refresh_tickets.get(pid, 0) + count

    def use_refresh_ticket(self, pid: str, current_time: float) -> bool:
        """个人刷新券：玩家用自己的券触发轮换，不用蹲全服固定刷新点。"""
        with self._lock:
            if self.refresh_tickets.get(pid, 0) <= 0:
                return False
            self.refresh_tickets[pid] -= 1
        self.refresh_shop(current_time)
        return True

    def _delist_expired(self, now: float):
        for item in self.items.values():
            if item.expires_at and now >= item.expires_at:
                item.delisted = True

    # ---- bug3/6/7: 限购 + 过期检查 + 原子库存扣减 ----
    def purchase(self, pid: str, iid: str, qty: int,
                 ts: float = None, member_level: int = 1) -> tuple[bool, str]:
        ts = time.time() if ts is None else ts
        with self._lock:  # 库存检查与扣减在同一临界区，并发只有一个成功
            item = self.items.get(iid)
            if item is None:
                return False, "no such item"
            if item.delisted or (item.expires_at and ts >= item.expires_at):
                item.delisted = True
                return False, "item expired"
            if qty <= 0:
                return False, "invalid qty"
            # 个人每日/每周限购
            history = self.personal_purchased.get((pid, iid), [])
            daily = sum(q for t, q in history if ts - t < self.DAY_SECONDS)
            weekly = sum(q for t, q in history if ts - t < self.WEEK_SECONDS)
            if daily + qty > item.personal_limit:
                return False, "personal daily limit exceeded"
            if weekly + qty > item.weekly_limit:
                return False, "personal weekly limit exceeded"
            # 全帮总量限购
            if self.guild_purchased.get(iid, 0) + qty > item.guild_limit:
                return False, "guild limit exceeded"
            if item.stock < qty:
                return False, "no stock"
            item.stock -= qty
            history.append((ts, qty))
            self.personal_purchased[(pid, iid)] = history
            self.guild_purchased[iid] = self.guild_purchased.get(iid, 0) + qty
            cost = self.get_price(iid, self.guild_level,
                                  self.tech_discount, member_level) * qty
            self.contributions[pid] = self.contributions.get(pid, 0) - cost
            self._log_contribution(pid, -cost, f"purchase:{iid}", ts, pid)
            return True, "ok"

    # ---- bug4: 价格随帮派等级、科技折扣、会员等级浮动 ----
    def get_price(self, iid: str, guild_level: int,
                  tech_discount: float = 0.0, member_level: int = 1) -> int:
        base = self.items[iid].price
        guild_disc = min(self.MAX_GUILD_DISCOUNT,
                         self.GUILD_DISCOUNT_PER_LEVEL * max(0, guild_level - 1))
        member_disc = min(self.MAX_MEMBER_DISCOUNT,
                          self.MEMBER_DISCOUNT_PER_LEVEL * max(0, member_level - 1))
        tech_disc = min(0.5, max(0.0, tech_discount))
        factor = (1 - guild_disc) * (1 - tech_disc) * (1 - member_disc)
        return max(1, int(base * factor))

    # ---- bug5: 贡献不可转让，只能通过帮派活动获取 ----
    def transfer_contribution(self, from_pid: str, to_pid: str, amount: int) -> bool:
        return False

    # ---- bug6: 过期商品自动下架且不可兑换 ----
    def is_item_available(self, iid: str, current_time: float) -> bool:
        item = self.items.get(iid)
        if item is None or item.delisted:
            return False
        if item.expires_at and current_time >= item.expires_at:
            item.delisted = True
            return False
        return True

    # ---- bug8: 帮派解散时归档商店数据，重建后是空商店 ----
    def archive_on_disband(self, guild_id: str):
        with self._lock:
            self.archive[guild_id] = {
                "items": dict(self.items),
                "contribution_log": list(self.contribution_log),
                "guild_purchased": dict(self.guild_purchased),
                "archived_at": time.time(),
            }
            self.items.clear()
            self.rotation_pool = []
            self.rotation_index = 0
            self.personal_purchased.clear()
            self.guild_purchased.clear()
            self.next_refresh = 0
