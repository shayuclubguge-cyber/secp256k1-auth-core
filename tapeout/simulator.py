"""
TapeOut 本地仿真器（阶段4升级版）

与链上 eval / beat 语义一致，并支持真正的寄存器反馈环：

  两遍 LATCH 语义
  ---------------
  1. eval 开始时，所有 LATCH 的输出信号预填为「上一拍存储的旧值」，
     因此组合逻辑可以引用任何 LATCH 输出（无论指令顺序）。
  2. 顺序执行所有指令（NAND / REF 立即计算）。
  3. eval 结束时，统一采样每个 LATCH 的输入 d，作为下一拍的值。

  这使得「占位 LATCH（latch_reg）+ 组合逻辑 + drive_latch 闭环」
  的标准时序设计模式可以工作，且与链上"心跳时读旧状态、写新状态"
  的描述一致。

  函数式 REF
  ----------
  REFResolver 除注册子电路外，还可注册 Python 函数（register_function），
  用于对尚未门级展开的子电路（如模逆、Keccak）做分层验证。
"""

from typing import Callable, Dict, List, Optional, Tuple

from . import Circuit, SIG_ZERO, SIG_ONE


class Simulator:
    """逐拍仿真器，支持有状态电路（LATCH 反馈环）"""

    def __init__(self, circuit: Circuit):
        self.circuit = circuit
        self.latch_values: Dict[int, bool] = {}
        self.cycle = 0
        self._explicit_outputs: Optional[List[int]] = None
        self.state: List = []
        self._build_plan()

    def _build_plan(self):
        """静态分析：为每条指令预计算输出信号ID，把指令编译为执行计划"""
        self._plan = []
        nid = 2 + self.circuit.n_in
        for inst in self.circuit.instructions:
            op = inst[0]
            if op == 0:  # NAND
                self._plan.append((0, nid, inst[1], inst[2]))
                nid += 1
            elif op == 1:  # LATCH
                self._plan.append((1, nid, inst[1]))
                nid += 1
            elif op == 2:  # REF
                _, cpu_addr, circuit_id, n_in, n_out, in_sigs = inst
                outs = list(range(nid, nid + n_out))
                self._plan.append(
                    (2, outs, cpu_addr, circuit_id, n_in, n_out, in_sigs)
                )
                nid += n_out
            else:
                raise ValueError(f"未知指令类型: {op}")
        self._n_signals = nid
        # 输出信号：显式指定优先，否则取最后 n_out 个
        self._default_outputs = list(range(nid - self.circuit.n_out, nid))

    def set_outputs(self, signals: List[int]):
        """显式指定输出信号ID（用于本地验证）"""
        self._explicit_outputs = signals

    def _get(self, sid: int) -> bool:
        """读取信号值（兼容旧式 dict-state 探索性子类）"""
        if sid == SIG_ZERO:
            return False
        if sid == SIG_ONE:
            return True
        return self.state[sid]

    def reset(self):
        """重置状态（LATCH 存储值与节拍计数）"""
        self.latch_values = {}
        self.cycle = 0
        self.state = []

    # --- 指令执行钩子（子类可覆写以支持 REF） ---
    def _exec_ref(self, state, cpu_addr, circuit_id, n_in, n_out, in_sigs):
        raise NotImplementedError(
            f"REF 指令需要外部电路定义 (cpu={cpu_addr.hex()}, cid={circuit_id})"
        )

    def eval(self, inputs: List[bool]) -> List[bool]:
        """
        单拍 eval：组合逻辑求值 + LATCH 采样（两遍语义，见模块 docstring）
        """
        circuit = self.circuit
        assert len(inputs) == circuit.n_in, \
            f"输入长度 {len(inputs)} != {circuit.n_in}"

        state = [None] * self._n_signals
        state[SIG_ZERO] = False
        state[SIG_ONE] = True
        for i, val in enumerate(inputs):
            state[2 + i] = bool(val)

        # 第1遍：预填所有 LATCH 输出为上一拍旧值
        lv = self.latch_values
        plan = self._plan
        for entry in plan:
            if entry[0] == 1:
                state[entry[1]] = lv.get(entry[1], False)

        # 第2遍：顺序执行（NAND / REF 立即计算，LATCH 只登记采样）
        latch_caps = []  # (out_id, d_id)
        st = state
        for entry in plan:
            op = entry[0]
            if op == 0:  # NAND
                _, out, a, b = entry
                va = st[a]
                vb = st[b]
                if va is None or vb is None:
                    raise KeyError(f"信号 {a} 或 {b} 未定义（NAND 输入悬空）")
                st[out] = not (va and vb)
            elif op == 1:  # LATCH：输出已预填，登记 d 采样
                latch_caps.append((entry[1], entry[2]))
            else:  # REF
                _, outs, cpu_addr, cid, n_in, n_out, in_sigs = entry
                self._exec_ref(st, cpu_addr, cid, n_in, n_out, in_sigs)

        # eval 结束：统一采样 LATCH 输入（下一拍生效）
        for out_id, d_id in latch_caps:
            v = st[d_id]
            if v is None:
                raise KeyError(f"LATCH 输入信号 {d_id} 未定义")
            lv[out_id] = v

        self.state = state
        out_ids = self._explicit_outputs or self._default_outputs
        return [st[s] for s in out_ids]

    def beat(self, inputs: List[bool]) -> List[bool]:
        """心跳一拍：eval 组合逻辑并推进 LATCH 状态"""
        result = self.eval(inputs)
        self.cycle += 1
        return result

    def run(self, input_sequence: List[List[bool]]) -> List[List[bool]]:
        """运行多拍输入序列"""
        return [self.beat(inp) for inp in input_sequence]


class REFResolver:
    """
    REF 解析器：管理外部电路引用
    - register():          注册子电路（本地等效网表，递归仿真）
    - register_function(): 注册 Python 函数 fn(bits)->bits（分层验证用）
    """

    def __init__(self):
        self._circuits: Dict[Tuple[bytes, int], Circuit] = {}
        self._functions: Dict[Tuple[bytes, int], Callable] = {}

    def register(self, cpu_addr: bytes, circuit_id: int, circuit: Circuit):
        self._circuits[(cpu_addr, circuit_id)] = circuit

    def register_function(self, cpu_addr: bytes, circuit_id: int,
                          fn: Callable[[List[bool]], List[bool]]):
        self._functions[(cpu_addr, circuit_id)] = fn

    def resolve(self, cpu_addr: bytes, circuit_id: int):
        key = (cpu_addr, circuit_id)
        if key in self._circuits:
            return ("circuit", self._circuits[key])
        if key in self._functions:
            return ("function", self._functions[key])
        return None


class SimWithREF(Simulator):
    """支持 REF 的仿真器（子电路递归仿真 / 函数式仿真）"""

    def __init__(self, circuit: Circuit, resolver: REFResolver):
        self.resolver = resolver
        self._sub_sims: Dict[Tuple[bytes, int], Simulator] = {}
        super().__init__(circuit)

    def _exec_ref(self, state, cpu_addr, circuit_id, n_in, n_out, in_sigs):
        # eval() 已内联 REF 处理；此路径不应到达
        raise RuntimeError("internal: SimWithREF 应走 eval 内联 REF 路径")

    def eval(self, inputs: List[bool]) -> List[bool]:
        circuit = self.circuit
        assert len(inputs) == circuit.n_in

        state = [None] * self._n_signals
        state[SIG_ZERO] = False
        state[SIG_ONE] = True
        for i, val in enumerate(inputs):
            state[2 + i] = bool(val)

        lv = self.latch_values
        plan = self._plan
        for entry in plan:
            if entry[0] == 1:
                state[entry[1]] = lv.get(entry[1], False)

        latch_caps = []
        st = state
        for entry in plan:
            op = entry[0]
            if op == 0:
                _, out, a, b = entry
                va = st[a]
                vb = st[b]
                if va is None or vb is None:
                    raise KeyError(f"信号 {a} 或 {b} 未定义（NAND 输入悬空）")
                st[out] = not (va and vb)
            elif op == 1:
                latch_caps.append((entry[1], entry[2]))
            else:
                _, outs, cpu_addr, cid, n_in, n_out, in_sigs = entry
                resolved = self.resolver.resolve(cpu_addr, cid)
                if resolved is None:
                    raise RuntimeError(
                        f"未注册 REF: cpu={cpu_addr.hex()}, cid={cid}"
                    )
                kind, target = resolved
                sub_in = []
                for s in in_sigs:
                    v = st[s]
                    if v is None:
                        raise KeyError(f"REF 输入信号 {s} 未定义")
                    sub_in.append(v)
                if kind == "function":
                    sub_out = target(sub_in)
                    if len(sub_out) != n_out:
                        raise AssertionError(
                            f"函数式 REF 输出长度 {len(sub_out)} != {n_out}"
                        )
                else:
                    key = (cpu_addr, cid)
                    if key not in self._sub_sims:
                        self._sub_sims[key] = SimWithREF(target, self.resolver)
                    sub_out = self._sub_sims[key].eval(sub_in)
                for o, v in zip(outs, sub_out):
                    st[o] = bool(v)

        for out_id, d_id in latch_caps:
            v = st[d_id]
            if v is None:
                raise KeyError(f"LATCH 输入信号 {d_id} 未定义")
            lv[out_id] = v

        self.state = state
        out_ids = self._explicit_outputs or self._default_outputs
        return [st[s] for s in out_ids]

    def reset(self):
        super().reset()
        self._sub_sims = {}
