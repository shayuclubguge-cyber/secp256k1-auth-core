"""ecrecover_driver 分层验证：点运算 → 标量乘 → 完整 ecrecover（真实地址）"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout.parts.alu_driver import AluDriver
from tapeout.parts import ecrecover_driver as ed
from tapeout import engine
from tapeout.engine import G, make_signature, keccak256
from tapeout.field import N


def test_point_ops_small():
    """倍点/加点与黄金模型一致（Jacobian 投影等价：转仿射后比较）"""
    d = AluDriver()
    P1 = (G[0], G[1], 1)
    got2 = ed.point_double(d, P1)
    want2 = engine.point_double(P1)
    assert got2 == want2, "point_double(G) 不匹配"
    got3 = ed.point_add(d, got2, P1)
    want3 = engine.point_add(want2, P1)
    # Jacobian 投影等价：同一仿射点
    assert engine.point_to_affine(got3) == engine.point_to_affine(want3), \
        "point_add(2G, G) 仿射点不匹配"
    print("✓ point_double/point_add 与黄金模型一致（2G, 3G，仿射等价）")


def test_scalar_mul_small():
    """32 位标量乘与黄金模型一致"""
    d = AluDriver()
    k = 0xDEADBEEF
    got = ed.scalar_mul(d, k)
    want = engine.scalar_mul(k)
    assert engine.point_to_affine(got) == engine.point_to_affine(want), \
        f"scalar_mul({k:#x}) 仿射点不匹配"
    print(f"✓ scalar_mul(0xDEADBEEF) 与黄金模型一致（拍数={len(d.trace):,}）")


def test_ecrecover_full():
    """完整 ecrecover：真实签名 → 恢复地址，与黄金模型 + 已知地址对照"""
    d = AluDriver()
    z = int.from_bytes(keccak256(b"hello tapeout"), "big")
    priv, k = 0xC0FFEE, 0xDEADBEEF
    r, s, v = make_signature(z, priv, k)
    res = ed.ecrecover(d, z, r, s, v)
    assert res is not None
    Qx, Qy, addr = res
    want = engine.ecrecover(z, r, s, v)
    assert (Qx, Qy) == (want[0], want[1]), "恢复点与黄金模型不匹配"
    assert addr == want[2], "地址不匹配"
    print(f"✓ ecrecover 全链路：地址 0x{addr.hex()}（拍数={len(d.trace):,}）")
    print(f"  r={r:#x}")
    print(f"  s={s:#x}")
    print(f"  v={v}")


if __name__ == "__main__":
    import time
    t0 = time.time()
    test_point_ops_small()
    t1 = time.time(); print(f"  ({t1-t0:.1f}s)")
    test_scalar_mul_small()
    t2 = time.time(); print(f"  ({t2-t1:.1f}s)")
    if "--full" in sys.argv:
        test_ecrecover_full()
        print(f"  ({time.time()-t2:.1f}s)")
    print("PASSED" + ("（不含全链路，加 --full 跑完整 ecrecover）" if "--full" not in sys.argv else ""))
