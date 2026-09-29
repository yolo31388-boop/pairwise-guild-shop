"""帮派商店与贡献兑换系统 - 红态测试"""
import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guild.shop import GuildShop, ShopItem

class TestContributionLog:
    def test_contribution_changes_logged(self):
        gs = GuildShop()
        gs.add_contribution("p1", 100, "raid")
        assert len(gs.contribution_log) > 0  # bug1: 0

class TestShopRefreshRotation:
    def test_refresh_rotates_not_resets(self):
        gs = GuildShop()
        gs.items["i1"] = ShopItem("i1", "mat", 10, stock=5)
        gs.refresh_shop(100)
        # 应该有轮换机制，不是简单重置
        assert hasattr(gs, "rotation_pool") or gs.items["i1"].stock != 100

class TestPersonalLimit:
    def test_cannot_exceed_personal_limit(self):
        gs = GuildShop()
        gs.items["i1"] = ShopItem("i1", "mat", 10, stock=100, personal_limit=3)
        gs.purchase("p1", "i1", 3)
        result, _ = gs.purchase("p1", "i1", 1)
        assert result == False  # bug3: 还能买

class TestPriceDiscount:
    def test_higher_guild_level_gets_discount(self):
        gs = GuildShop()
        gs.items["i1"] = ShopItem("i1", "mat", 100, stock=10)
        p1 = gs.get_price("i1", guild_level=1)
        p10 = gs.get_price("i1", guild_level=10)
        assert p10 < p1  # bug4: 都是100

class TestNoTransfer:
    def test_contribution_cannot_be_transferred(self):
        gs = GuildShop()
        assert gs.transfer_contribution("p1", "p2", 100) == False  # bug5: True

class TestExpiredItem:
    def test_expired_item_not_available(self):
        gs = GuildShop()
        gs.items["i1"] = ShopItem("i1", "event", 10, stock=10, expires_at=100)
        assert gs.is_item_available("i1", 200) == False  # bug6: True

class TestAtomicPurchase:
    def test_concurrent_purchase_no_oversell(self):
        gs = GuildShop()
        gs.items["i1"] = ShopItem("i1", "mat", 10, stock=1)
        r1, _ = gs.purchase("p1", "i1", 1)
        r2, _ = gs.purchase("p2", "i1", 1)
        assert not (r1 and r2)  # bug7: 可能都成功
        assert gs.items["i1"].stock >= 0

class TestArchiveOnDisband:
    def test_disband_archives_shop(self):
        gs = GuildShop()
        gs.items["i1"] = ShopItem("i1", "mat", 10, stock=10)
        gs.archive_on_disband("g1")
        assert hasattr(gs, "archive") and "g1" in gs.archive  # bug8: 空
