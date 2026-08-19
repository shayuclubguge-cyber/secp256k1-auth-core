"""
ecrecover_ctrl —— 旗舰主电路：宏指令展开器 + 五路 REF 组装

把 ECRECOVER 编译成的「宏指令流」（每条 1 拍从引脚注入）展开成精确拍级
控制序列，驱动五颗已上链零件完成全部 256 位模运算：

  REF ringbusA (cid4) / ringbusB (cid5)  —— 10×256 位寄存器堆
  REF piso256  (cid3)   —— B 操作数位流器（喂 mcore LD_B）
  REF mcore256 (cid2)   —— 模乘引擎（内部再 REF fadd64 cid1，深度 2 已验证）
  REF fadd64b  (cid6)   —— 顶层加/减/比较 ALU

宏指令（EXEC 接受拍随 in_word 注入，16 位）：
  bits[3:0]   opcode: 0 NOP | 1 MOV | 2 EQ0 | 3 NE0 | 4 LTCHK | 5 ADD | 6 SUB | 7 MUL
  bits[7:4]   srcA    bits[11:8] srcB    bits[15:12] dst
操作数编码（bit3=环选择，bit2..0=环内地址，零译码成本）：
  0..4 = ringbusA（X/Y/Z/T1/R0），8..12 = ringbusB（BX/BY/T2/T3/T4），
  14 = 引脚直供（公开常数字流），15 = NULL（不写回）。

语义（m 由驱动器在常数窗口喂入，p/n 通用）：
  MOV   dst = srcA
  EQ0   srcA != 0 → fail 置位        NE0  srcA == 0 → fail 置位
  LTCHK srcA >= srcB → fail 置位（范围检查）
  ADD   dst = srcA + srcB mod m（溢出 +rc 补偿、SUB m 约减、借位还原全自动）
  SUB   dst = srcA - srcB mod m（借位 +m 还原自动）
  MUL   dst = srcA * srcB mod m（mcore256 全流程，~1.3k-4.4k 拍）

顶层命令（cmd；IDLE 且 ph3 拍接受，aux 随 in_word）：
  1 WRITE_RING  aux[3:0]=dst，随后 4 拍字流入环
  2 EXEC        aux[15:0]=宏指令，展开执行，done 脉冲 4 拍结束
  3 READ_RING   aux[3:0]=src，4 拍 out_word 流出（LSW 先行）

引脚：68 入（in_word64/cmd3/start）/ 70 出
（out_word64/busy/done/fail/fadd_cout/mcore_busy/mcore_done）。

关键结构决策：
  - 写回缓冲环 buf[256]：mcore/fadd/MOV 的结果先捕获进 buf（4 拍顶装），
    再用独立 WB 帧（ph0 对齐）写回寄存器堆 —— 断开「REF 原子求值环」
    （ringbus 输入依赖 mcore 输出、后者又依赖 ringbus 输出）
  - mcore 命令接受闸 = ph3 & m_idle_r（mcore 空闲指示打一拍寄存，
    避免 mstart 组合依赖 mcore 输出的指令序环）
  - 全系统 4 拍网格：所有传输窗口 ph0 起，迁移只在 ph3 拍末发生
"""

from typing import Dict, List

from .. import Circuit, Signal

# 宏指令操作码
OP_NOP, OP_MOV, OP_EQ0, OP_NE0, OP_LTCHK, OP_ADD, OP_SUB, OP_MUL = range(8)
PIN, NULL = 14, 15
CMD_WRITE, CMD_EXEC, CMD_READ = 1, 2, 3
MC_LD_A, MC_LD_B, MC_LD_C, MC_RUN, MC_READ, MC_REDUCE = 0, 1, 2, 3, 4, 5
FA_LOAD, FA_ADD, FA_SUB = 0, 1, 2

(S_IDLE, S_WR, S_RD, S_DONE, S_MV, S_WB, S_EQ,
 S_LC1, S_LC2,
 S_A1, S_A2, S_A3, S_A4, S_A5,
 S_S1, S_S2, S_S3,
 S_M1, S_M2, S_M3, S_M4, S_M5, S_M6, S_M7, S_M8,
 S_M9, S_M10, S_M11, S_M12, S_M13, S_M14) = range(31)

DEFAULT_CIDS = dict(mcore=2, piso=3, ringA=4, ringB=5, faddb=6)


def _mux3(c: Circuit, a: Signal, b: Signal, sel: Signal, sel_n: Signal) -> Signal:
    """3-NAND MUX（共享反相）：sel=0 选 a，sel=1 选 b"""
    t1 = c.nand(a, sel_n)
    t2 = c.nand(b, sel)
    return c.nand(t1, t2)


def build_ecrecover_ctrl(child_cpu: bytes,
                         cids: Dict[str, int] = DEFAULT_CIDS) -> Circuit:
    c = Circuit("ecrecover_ctrl", n_in=68, n_out=70)
    pins = c.input_signals()
    in_word = pins[0:64]
    cmd = pins[64:67]
    start = pins[67]
    Z = c.zero()

    # ---------------- 状态寄存器 ----------------
    st = c.latch_bus(5)
    ph = c.latch_bus(2)
    fr = c.latch_bus(6)          # M6 帧计数 0..63
    op = c.latch_bus(4)
    sa = c.latch_bus(4)
    sb = c.latch_bus(4)
    dst = c.latch_bus(4)
    or_acc = c.latch_reg()
    fail = c.latch_reg()
    m_idle_r = c.latch_reg()     # mcore 空闲指示（打一拍）
    buf = c.latch_bus(256)       # 写回缓冲环（rot64 + 捕获顶装）

    # ---------------- 早段组合：状态译码 ----------------
    st_n = [c.not_(st[i]) for i in range(5)]

    def st_eq(k: int) -> Signal:
        t = c.one()
        for i in range(5):
            t = c.and_(t, st[i] if (k >> i) & 1 else st_n[i])
        return t

    is_idle = st_eq(S_IDLE)
    is_wr = st_eq(S_WR)
    is_rd = st_eq(S_RD)
    is_done = st_eq(S_DONE)
    is_mv = st_eq(S_MV)
    is_wb = st_eq(S_WB)
    is_eq = st_eq(S_EQ)
    is_lc1 = st_eq(S_LC1)
    is_lc2 = st_eq(S_LC2)
    is_a1 = st_eq(S_A1)
    is_a2 = st_eq(S_A2)
    is_a3 = st_eq(S_A3)
    is_a4 = st_eq(S_A4)
    is_a5 = st_eq(S_A5)
    is_s1 = st_eq(S_S1)
    is_s2 = st_eq(S_S2)
    is_s3 = st_eq(S_S3)
    is_m1 = st_eq(S_M1)
    is_m2 = st_eq(S_M2)
    is_m3 = st_eq(S_M3)
    is_m4 = st_eq(S_M4)
    is_m5 = st_eq(S_M5)
    is_m6 = st_eq(S_M6)
    is_m7 = st_eq(S_M7)
    is_m8 = st_eq(S_M8)
    is_m9 = st_eq(S_M9)
    is_m10 = st_eq(S_M10)
    is_m11 = st_eq(S_M11)
    is_m12 = st_eq(S_M12)
    is_m13 = st_eq(S_M13)
    is_m14 = st_eq(S_M14)

    ph0 = c.and_(c.not_(ph[0]), c.not_(ph[1]))
    ph3 = c.and_(ph[0], ph[1])
    ph3_n = c.not_(ph3)

    # mcore 命令接受态（自对齐：等到 ph3 且 mcore 空闲（打一拍））
    is_macc = c.or_(c.or_(c.or_(is_m1, is_m3), c.or_(is_m7, is_m9)),
                    c.or_(is_m11, is_m13))
    g_acc = c.and_(ph3, m_idle_r)

    # ---------------- 取数选择 ----------------
    fetch_sa = c.or_(c.or_(c.or_(is_mv, is_eq), c.or_(is_lc1, is_a1)),
                     c.or_(is_s1, is_m4))
    fetch_sb = c.or_(c.or_(is_lc2, is_a2), c.or_(is_s2, is_m5))
    fetch_dst = is_rd
    fsrc = [c.or_(c.or_(c.and_(sa[i], fetch_sa), c.and_(sb[i], fetch_sb)),
                  c.and_(dst[i], fetch_dst)) for i in range(4)]
    fsrc_is_pin = c.and_(c.and_(fsrc[3], fsrc[2]),
                         c.and_(fsrc[1], c.not_(fsrc[0])))

    # fadd 常数帧 / mcore 常数帧 → 操作数强制引脚
    fadd_const = c.or_(c.or_(is_a3, is_a4), c.or_(is_a5, is_s3))
    mcore_const = c.or_(is_m2, is_m10)
    opnd_sel = c.or_(fsrc_is_pin, c.or_(fadd_const, mcore_const))
    opnd_sel_n = c.not_(opnd_sel)

    # 写环：WR 帧（引脚直写）与 WB 帧（buf 写回）
    we_state = c.or_(is_wr, is_wb)
    dst_null = c.and_(c.and_(dst[0], dst[1]), c.and_(dst[2], dst[3]))
    we_base = c.and_(we_state, c.not_(dst_null))
    weA = c.and_(we_base, c.not_(dst[3]))
    weB = c.and_(we_base, dst[3])
    raddr = [fsrc[0], fsrc[1], fsrc[2]]

    # 写回总线：WB = buf 底片（捕获结束后 slice0=word0，随拍旋转对齐 ph==w）；
    # WR = 引脚
    is_wb_n = c.not_(is_wb)
    ring_bus = [_mux3(c, in_word[k], buf[k], is_wb, is_wb_n)
                for k in range(64)]

    # ---------------- REF 1/2：寄存器堆 ----------------
    rbA = c.ref(child_cpu, cids["ringA"], 71, 64,
                ring_bus + [dst[0], dst[1], dst[2]] + [weA] + raddr)
    rbB = c.ref(child_cpu, cids["ringB"], 71, 64,
                ring_bus + [dst[0], dst[1], dst[2]] + [weB] + raddr)

    # 取数字合并 + 操作数选择
    fs3_n = c.not_(fsrc[3])
    fetch_word = [_mux3(c, rbA[k], rbB[k], fsrc[3], fs3_n) for k in range(64)]
    operand = [_mux3(c, fetch_word[k], in_word[k], opnd_sel, opnd_sel_n)
               for k in range(64)]

    # ---------------- REF 3：piso256 ----------------
    piso = c.ref(child_cpu, cids["piso"], 66, 1,
                 operand + [is_m5, is_m6])
    piso_bit = piso[0]

    # ---------------- REF 4：mcore256 ----------------
    # mcmd：M1/M9=LD_C, M3=LD_A, M5=LD_B, M7=RUN, M11=REDUCE, M13=READ
    mc_cmd_bits = [  # bit0: LD_B(1)/RUN(3)/REDUCE(5) → M5/M7/M11
        c.or_(c.or_(is_m5, is_m7), is_m11),
        # bit1: LD_C(2)/RUN(3) → M1/M9/M7
        c.or_(c.or_(is_m1, is_m9), is_m7),
        # bit2: READ(4)/REDUCE(5) → M13/M11
        c.or_(is_m13, is_m11),
    ]
    mstart = c.or_(c.and_(is_macc, g_acc), c.and_(is_m5, ph3))
    m6_n = c.not_(is_m6)
    m_pin0 = _mux3(c, operand[0], piso_bit, is_m6, m6_n)
    mcore = c.ref(child_cpu, cids["mcore"], 68, 66,
                  [m_pin0] + operand[1:64] + mc_cmd_bits + [mstart])
    m_out_word = mcore[0:64]
    m_busy = mcore[64]
    m_done = mcore[65]

    # ---------------- REF 5：fadd64b ----------------
    # 帧映射：LC1/A1/S1=LOAD, A2/A3/A5/S3=ADD, LC2/A4/S2=SUB
    fa_is_load = c.or_(c.or_(is_lc1, is_a1), is_s1)
    fa_is_add = c.or_(c.or_(is_a2, is_a3), c.or_(is_a5, is_s3))
    fa_is_sub = c.or_(c.or_(is_lc2, is_a4), is_s2)
    fa_frame = c.or_(c.or_(fa_is_load, fa_is_add), fa_is_sub)
    fa_op0 = fa_is_add
    fa_op1 = fa_is_sub
    fa_start = c.and_(fa_frame, ph0)
    fadd = c.ref(child_cpu, cids["faddb"], 67, 65,
                 operand + [fa_op0, fa_op1, fa_start])
    fa_out_word = fadd[0:64]
    fa_cout = fadd[64]

    # ---------------- 晚段组合：捕获 + 迁移 ----------------
    # 捕获字：M14=mcore.out，A4/A5/S2/S3=fadd.out，MV=取数字
    cap_mcore = is_m14
    cap_fadd = c.or_(c.or_(is_a4, is_a5), c.or_(is_s2, is_s3))
    cap_fetch = is_mv
    cap_state = c.or_(c.or_(cap_mcore, cap_fadd), cap_fetch)
    cap_mcore_n = c.not_(cap_mcore)
    cap_fadd_n = c.not_(cap_fadd)
    cap_fetch_n = c.not_(cap_fetch)
    cap_word = []
    for k in range(64):
        t = _mux3(c, fetch_word[k], m_out_word[k], cap_mcore, cap_mcore_n)
        t = _mux3(c, t, fa_out_word[k], cap_fadd, cap_fadd_n)
        cap_word.append(_mux3(c, t, fetch_word[k], cap_fetch, cap_fetch_n))

    # EQ 的 OR 归约（对取数字，4 拍累加）
    or_tree = fetch_word[0]
    for k in range(1, 64):
        or_tree = c.or_(or_tree, fetch_word[k])
    eq_now = c.and_(is_eq, c.or_(or_acc, or_tree))   # 截至本拍的全 OR

    # 状态迁移（全部在 ph3 拍末；M8/M12 由 m_done 驱动随时迁移）
    nxt = [Z] * 5

    def merge(k: int, en: Signal):
        for i in range(5):
            if (k >> i) & 1:
                nxt[i] = c.or_(nxt[i], en)

    # IDLE：命令接受
    acc = c.and_(c.and_(is_idle, start), ph3)
    cmd_n = [c.not_(cmd[i]) for i in range(3)]
    acc_wr = c.and_(acc, c.and_(cmd_n[2], c.and_(cmd_n[1], cmd[0])))      # 001
    acc_exec = c.and_(acc, c.and_(cmd_n[2], c.and_(cmd[1], cmd_n[0])))    # 010
    acc_rd = c.and_(acc, c.and_(cmd_n[2], c.and_(cmd[1], cmd[0])))        # 011
    # EXEC 分派（op 字段直接读引脚 in_word[3:0]）
    opw = in_word[0:4]
    opw_n = [c.not_(b) for b in opw]

    def op_is(v: int) -> Signal:
        t = acc_exec
        for i in range(4):
            t = c.and_(t, opw[i] if (v >> i) & 1 else opw_n[i])
        return t

    merge(S_WR, acc_wr)
    merge(S_RD, acc_rd)
    merge(S_MV, op_is(OP_MOV))
    merge(S_EQ, c.or_(op_is(OP_EQ0), op_is(OP_NE0)))
    merge(S_LC1, op_is(OP_LTCHK))
    merge(S_A1, op_is(OP_ADD))
    merge(S_S1, op_is(OP_SUB))
    merge(S_M1, op_is(OP_MUL))
    merge(S_DONE, op_is(OP_NOP))
    merge(S_IDLE, c.and_(is_idle, c.not_(acc)))      # 未接受保持（任意拍）

    # 固定 4 拍帧的转移
    fr_frame_ph3 = ph3
    merge(S_DONE, c.and_(c.or_(c.or_(is_wr, is_rd), c.or_(is_eq, is_wb)),
                         fr_frame_ph3))
    merge(S_IDLE, c.and_(is_done, fr_frame_ph3))     # DONE 4 拍后回 IDLE
    merge(S_WB, c.and_(is_mv, fr_frame_ph3))
    merge(S_LC2, c.and_(is_lc1, fr_frame_ph3))
    # LC2 结束：fail |= cout（srcA >= srcB）
    merge(S_DONE, c.and_(is_lc2, fr_frame_ph3))
    # ADD 序列
    merge(S_A2, c.and_(is_a1, fr_frame_ph3))
    a2_end = c.and_(is_a2, fr_frame_ph3)
    merge(S_A3, c.and_(a2_end, fa_cout))             # 溢出 → +rc 补偿
    merge(S_A4, c.and_(a2_end, c.not_(fa_cout)))
    merge(S_A4, c.and_(is_a3, fr_frame_ph3))
    a4_end = c.and_(is_a4, fr_frame_ph3)
    merge(S_WB, c.and_(a4_end, fa_cout))             # 约减成功
    merge(S_A5, c.and_(a4_end, c.not_(fa_cout)))     # 借位 → +m 还原
    merge(S_WB, c.and_(is_a5, fr_frame_ph3))
    # SUB 序列
    merge(S_S2, c.and_(is_s1, fr_frame_ph3))
    s2_end = c.and_(is_s2, fr_frame_ph3)
    merge(S_WB, c.and_(s2_end, fa_cout))
    merge(S_S3, c.and_(s2_end, c.not_(fa_cout)))
    merge(S_WB, c.and_(is_s3, fr_frame_ph3))
    # MUL 序列（mcore 命令接受态：g_acc 拍迁移，未接受则自保持）
    g_acc_n = c.not_(g_acc)
    merge(S_M2, c.and_(is_m1, g_acc))
    merge(S_M1, c.and_(is_m1, g_acc_n))
    merge(S_M3, c.and_(is_m2, fr_frame_ph3))
    merge(S_M4, c.and_(is_m3, g_acc))
    merge(S_M3, c.and_(is_m3, g_acc_n))
    merge(S_M5, c.and_(is_m4, fr_frame_ph3))
    merge(S_M6, c.and_(is_m5, fr_frame_ph3))         # 同拍已发 LD_B
    fr63 = c.and_(c.and_(c.and_(fr[0], fr[1]), c.and_(fr[2], fr[3])),
                  c.and_(fr[4], fr[5]))
    merge(S_M7, c.and_(c.and_(is_m6, fr63), fr_frame_ph3))
    merge(S_M6, c.and_(c.and_(is_m6, c.not_(fr63)), fr_frame_ph3))
    merge(S_M8, c.and_(is_m7, g_acc))
    merge(S_M7, c.and_(is_m7, g_acc_n))
    merge(S_M9, c.and_(is_m8, m_done))               # 随时：done 出现即走
    merge(S_M8, c.and_(is_m8, c.not_(m_done)))       # 等待自保持（含 ph3）
    merge(S_M10, c.and_(is_m9, g_acc))
    merge(S_M9, c.and_(is_m9, g_acc_n))
    merge(S_M11, c.and_(is_m10, fr_frame_ph3))
    merge(S_M12, c.and_(is_m11, g_acc))
    merge(S_M11, c.and_(is_m11, g_acc_n))
    merge(S_M13, c.and_(is_m12, m_done))
    merge(S_M12, c.and_(is_m12, c.not_(m_done)))
    merge(S_M14, c.and_(is_m13, g_acc))
    merge(S_M13, c.and_(is_m13, g_acc_n))
    merge(S_WB, c.and_(is_m14, fr_frame_ph3))
    # 保持项（未迁移的态）
    hold = c.not_(ph3)
    st_d = [_mux3(c, nxt[i], st[i], ph3_n, ph3) for i in range(5)]
    # M8/M12 的 m_done 迁移不受 ph3 闸限制 → 特殊处理：见下修补
    # （st_d 之上再叠 m_done 旁路）
    m8_go = c.and_(is_m8, m_done)
    m12_go = c.and_(is_m12, m_done)
    bypass = c.or_(m8_go, m12_go)
    bypass_n = c.not_(bypass)
    # bypass 目标：M9 / M13
    bxt = [Z] * 5
    for i in range(5):
        if (S_M9 >> i) & 1:
            bxt[i] = c.or_(bxt[i], m8_go)
        if (S_M13 >> i) & 1:
            bxt[i] = c.or_(bxt[i], m12_go)
    st_d = [_mux3(c, st_d[i], bxt[i], bypass, bypass_n) for i in range(5)]

    # ph 自由运行
    ph_d = [c.not_(ph[0]), c.xor(ph[0], ph[1])]

    # fr：M6 内每个 ph3 +1；进入 M6 时清零（M5 的 ph3）
    fr_inc = c.and_(is_m6, ph3)
    fr_clr = c.and_(is_m5, ph3)
    cry = c.one()
    fr_d: List[Signal] = []
    fr_inc_n = c.not_(fr_inc)
    fr_clr_n = c.not_(fr_clr)
    for i in range(6):
        inc_i = c.xor(fr[i], cry)
        cry = c.and_(fr[i], cry)
        t = _mux3(c, fr[i], inc_i, fr_inc, fr_inc_n)
        fr_d.append(c.and_(t, fr_clr_n))

    # op/sa/sb/dst：EXEC/WRITE/READ 接受拍锁存
    latch_fields = c.or_(c.or_(acc_wr, acc_exec), acc_rd)
    lf_n = c.not_(latch_fields)
    op_d = [_mux3(c, op[i], in_word[i], latch_fields, lf_n) for i in range(4)]
    sa_d = [_mux3(c, sa[i], in_word[4 + i], latch_fields, lf_n) for i in range(4)]
    sb_d = [_mux3(c, sb[i], in_word[8 + i], latch_fields, lf_n) for i in range(4)]
    dst_d = [_mux3(c, dst[i], in_word[12 + i], latch_fields, lf_n) for i in range(4)]

    # or_acc：EQ 帧累加；进入 EQ 时清零（acc_exec 接受拍）
    or_d = c.and_(c.or_(or_acc, c.and_(is_eq, or_tree)), c.not_(acc_exec))

    # fail：LC2 末拍 |= cout；EQ 末拍按 op 更新
    #   EQ0: 见到非零位（eq_now=1）→ fail；NE0: 全零（eq_now=0）→ fail
    eq_end = c.and_(is_eq, ph3)
    is_ne0 = c.and_(c.and_(op[0], op[1]), c.and_(c.not_(op[2]), c.not_(op[3])))
    fail_eq = c.and_(eq_end,
                     c.mux(is_ne0, eq_now, c.not_(eq_now)))
    fail_d = c.or_(fail, c.or_(fail_eq, c.and_(c.and_(is_lc2, ph3), fa_cout)))

    # m_idle_r：打一拍 mcore 空闲指示
    m_idle_ind = c.and_(c.not_(m_busy), c.not_(m_done))

    # buf：rot64 自由旋转 + 捕获态顶装
    cap_state_n = c.not_(cap_state)
    buf_d: List[Signal] = [buf[64 + k] for k in range(192)]
    buf_d += [_mux3(c, buf[j], cap_word[j], cap_state, cap_state_n)
              for j in range(64)]

    # ---------------- 输出 ----------------
    out_word = [c.and_(fetch_word[k], is_rd) for k in range(64)]
    busy = c.not_(c.or_(is_idle, is_done))
    c.seal_outputs(out_word + [busy, is_done, fail, fa_cout, m_busy, m_done])

    # ---------------- 闭环 ----------------
    c.drive_bus(st, st_d)
    c.drive_bus(ph, ph_d)
    c.drive_bus(fr, fr_d)
    c.drive_bus(op, op_d)
    c.drive_bus(sa, sa_d)
    c.drive_bus(sb, sb_d)
    c.drive_bus(dst, dst_d)
    c.drive_latch(or_acc, or_d)
    c.drive_latch(fail, fail_d)
    c.drive_latch(m_idle_r, m_idle_ind)
    c.drive_bus(buf, buf_d)
    return c
