"""
ecrecover_compiler L2 验证：编译器（影子执行）+ 关键 gadget 门级对拍

分层：
  --shadow : 完整 ecrecover 编译 + 影子执行，对照 engine 黄金模型 + 真实地址（秒级）
  --double : point_double 门级（7 MUL ≈ 20k 拍）
  --add    : point_add 门级（环驻/引脚两种基点，11 MUL ≈ 35k 拍）
  --pow    : powmod 小子指数门级（发射逻辑抽查）
默认全跑（门级部分较慢，可分项）。
"""

import sys
import time

sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1/tests")

from test_ecrecover_ctrl import build_sim, CtrlDriver

from tapeout import engine
from tapeout.engine import G, GX, GY, make_signature, keccak256
from tapeout.field import P, N
from tapeout.parts.ecrecover_compiler import (
    Compiler, run_program, X, Y, Z, T1, R0, BX, BY, T2, T3, T4, BASE_RING,
)

G3 = (GX, GY, 1)


def run_ops(ops):
    """门级执行一段程序，返回 driver（可读环）"""
    c, sim = build_sim()
    d = CtrlDriver(sim, m=P)
    for o in ops:
        if o[0] == "W":
            _, dst, val = o
            d.write_ring(dst, val)
        else:
            _, m, op, sa, sb, dst, pin = o
            d.m = m
            d.rc = (1 << 256) - m
            d.exec(op, sa, sb, dst,
                   pin_a=pin if sa == 14 else None,
                   pin_b=pin if sb == 14 else None)
    return d


def test_shadow_full():
    t0 = time.time()
    z = int.from_bytes(keccak256(b"hello tapeout"), "big")
    priv, k = 0xC0FFEE, 0xDEADBEEF
    r, s, v = make_signature(z, priv, k)
    rinv = pow(r, -1, N)

    comp = Compiler()
    comp.compile_ecrecover(z, r, s, v, rinv)
    print(f"编译: {comp.stats()}")

    want = engine.ecrecover(z, r, s, v)
    assert want is not None
    assert not comp.fail, "影子 fail 不应置位"
    assert (comp.qx, comp.qy) == (want[0], want[1]), \
        f"Qx/Qy 不匹配黄金模型\n got=({comp.qx:#x},{comp.qy:#x})\nwant=({want[0]:#x},{want[1]:#x})"
    pub = comp.qx.to_bytes(32, "big") + comp.qy.to_bytes(32, "big")
    addr = keccak256(pub)[12:]
    assert addr == want[2], "地址不匹配"
    print(f"✓ 完整 ecrecover 影子执行: 地址 0x{addr.hex()} 与黄金模型一致 "
          f"（{time.time()-t0:.1f}s）")
    print(f"  r={r:#x}\n  s={s:#x}\n  v={v}")

    # 负例：伪造 rinv（绕过编译期断言，直接构造 ops）→ 一致性检查必须 fail
    comp3 = Compiler()
    comp3.w(T1, r)
    comp3.w(T4, (rinv + 1) % N)
    comp3.e(N, 7, T4, T1, T1)          # MUL mod n
    comp3.e(N, 6, T1, 14, T1, pin=1)   # SUB 1
    comp3.e(N, 2, T1)                  # EQ0 → 应 fail
    assert comp3.fail, "篡改 rinv 未被检出"
    print("✓ 负例：篡改 rinv → fail 置位（信任洞已闭合）")


def test_point_double_gate():
    t0 = time.time()
    comp = Compiler()
    comp.w(X, GX)
    comp.w(Y, GY)
    comp.w(Z, 1)
    comp.point_double()
    d = run_ops(comp.ops)
    want = engine.point_double(G3)
    got = (d.read_ring(X), d.read_ring(Y), d.read_ring(Z))
    assert got == want, f"point_double 不匹配\n got={got}\nwant={want}"
    assert got == (comp.shadow[X], comp.shadow[Y], comp.shadow[Z]), \
        "门级与编译器影子不一致"
    print(f"✓ point_double 门级 == 黄金模型（{d.ticks:,} 拍, "
          f"{time.time()-t0:.1f}s）")


def test_point_add_gate():
    t0 = time.time()
    # 环驻基点：2G + G = 3G
    comp = Compiler()
    comp.w(X, GX)
    comp.w(Y, GY)
    comp.w(Z, 1)
    comp.point_double()
    comp.w(BX, GX)
    comp.w(BY, GY)
    comp.point_add(BASE_RING)
    d = run_ops(comp.ops)
    want = engine.point_add(engine.point_double(G3), G3)
    got = (d.read_ring(X), d.read_ring(Y), d.read_ring(Z))
    # 引擎与驱动器公式不同（同为合法 Jacobian 代表元）→ 比仿射
    assert engine.point_to_affine(got) == engine.point_to_affine(want), \
        f"point_add(环驻基点) 仿射不匹配"
    assert engine.point_to_affine(got) == engine.point_to_affine(
        (comp.shadow[X], comp.shadow[Y], comp.shadow[Z])), \
        "门级与编译器影子不一致"
    print(f"✓ point_add 环驻基点 门级 == 黄金模型（仿射）（{d.ticks:,} 拍, "
          f"{time.time()-t0:.1f}s）")

    # 引脚基点：3G + (−G) = 2G（负数常数走引脚）
    t1 = time.time()
    comp2 = Compiler()
    comp2.ops = comp.ops          # 复用前半段（影子一致）
    comp2.shadow = comp.shadow[:]
    comp2.point_add(("pin", GX, (P - GY) % P))
    d2 = run_ops(comp2.ops)
    want2 = engine.point_add(want, (GX, (P - GY) % P, 1))
    got2 = (d2.read_ring(X), d2.read_ring(Y), d2.read_ring(Z))
    assert engine.point_to_affine(got2) == engine.point_to_affine(want2), \
        "point_add(引脚基点) 仿射不匹配"
    # 3G + (−G) = 2G：仿射等价于 point_double(G)
    assert engine.point_to_affine(got2) == engine.point_to_affine(
        engine.point_double(G3))
    print(f"✓ point_add 引脚基点（含 −G 常数）门级正确（{d2.ticks:,} 拍, "
          f"{time.time()-t1:.1f}s）")


def test_powmod_gate():
    t0 = time.time()
    comp = Compiler()
    comp.w(T1, 7)
    comp.powmod(T2, T1, 13, P)        # 7^13 mod p
    d = run_ops(comp.ops)
    got = d.read_ring(T2)
    want = pow(7, 13, P)
    assert got == want == comp.shadow[T2]
    print(f"✓ powmod(7^13) 门级 == 黄金值（{d.ticks:,} 拍, "
          f"{time.time()-t0:.1f}s）")


if __name__ == "__main__":
    args = sys.argv[1:]
    run_all = not args
    if run_all or "--shadow" in args:
        test_shadow_full()
    if run_all or "--pow" in args:
        test_powmod_gate()
    if run_all or "--double" in args:
        test_point_double_gate()
    if run_all or "--add" in args:
        test_point_add_gate()
    print("PASSED")
