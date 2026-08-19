"""
流式 Keccak 封装核 MVP
引脚 ≤100，内部 1600位状态 + 单轮组合逻辑
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from typing import List
from tapeout import Circuit, Signal
from tapeout.simulator import Simulator
from test_keccak import keccak_f_round_ref, KECCAK_RC


def build_streaming_keccak() -> Circuit:
    """
    流式 Keccak 单轮处理器 MVP
    引脚:
      - data[0:63]   (64)  双向数据
      - lane_id[0:4] (5)   lane 编号 0-24
      - mode         (1)   0=LOAD, 1=RUN
      - start        (1)   触发（简化版中忽略，mode直接控制）
    输出:
      - data[0:63]   (64)  数据输出
      - ready        (1)   就绪（简化版中固定为1）
    总计: 71入 → 65出

    注意：此MVP电路需要外部控制拍序：
      1. 25拍 LOAD 模式：每拍提供 lane_id + data，写入内部状态
      2. 1拍 RUN 模式：执行单轮 Keccak-f，输出 lane 0 的结果
      3. 25拍 READ：每拍提供 lane_id，读取对应 lane
    """
    c = Circuit("streaming_keccak_mvp", n_in=71, n_out=65)

    pins = c.input_signals()
    data_in = pins[0:64]
    lane_id = pins[64:69]
    mode = pins[69]
    # start = pins[70]  # 简化版中忽略

    # --- 组合逻辑：单轮 Keccak-f ---
    # 输入：1600位状态（来自LATCH）+ 5位轮号
    # 先定义"虚拟"输入信号，后续用LATCH替换

    # 为简化，我们创建一个纯组合的"单轮处理器"
    # 1600位状态作为输入引脚... 但这会超过255引脚

    # 所以策略：1600位状态在内部用LATCH存储
    # 但LATCH的输出是信号，可以作为组合逻辑的输入

    # 先创建1605个"占位"信号（后续会被LATCH覆盖）
    # 实际上，我们先创建组合逻辑，输入使用占位信号
    # 然后创建LATCH，输出连接到占位信号的位置

    # 但电路构建是线性的：信号ID递增
    # 所以我们需要先创建LATCH，然后用MUX选择LATCH的输入

    # 正确顺序：
    # 1. 创建所有组合逻辑（MUX、单轮Keccak）
    # 2. 用组合逻辑的输出创建LATCH

    # 但单轮Keccak需要1600位状态输入。这些输入来自哪里？
    # 来自上一轮LATCH的输出。但第一轮没有上一轮...

    # 解决方案：LATCH初始值由外部提供（通过LOAD模式）
    # 在电路中，LATCH的输入是一个MUX：
    #   - LOAD模式：data_in
    #   - RUN模式：单轮输出
    #   - 其他：保持原值

    # 为简化MVP，我们把1600位状态分成25个64位lane
    # 每个lane的MUX选择：LOAD（写入data_in）或 RUN（写入单轮输出）或保持

    # 先计算lane_id解码
    lane_select = []
    for i in range(25):
        eq_bits = []
        for bit in range(5):
            i_bit = (i >> bit) & 1
            if i_bit:
                eq_bits.append(lane_id[bit])
            else:
                eq_bits.append(c.not_(lane_id[bit]))
        t = c.and_(eq_bits[0], eq_bits[1])
        t = c.and_(t, eq_bits[2])
        t = c.and_(t, eq_bits[3])
        t = c.and_(t, eq_bits[4])
        lane_select.append(t)

    # 单轮 Keccak 组合逻辑（输入为占位信号，后续替换）
    # 为简化，我们不在这里展开完整的单轮逻辑
    # 而是创建一个简化版：假设状态已经存储，输出新的状态

    # 实际上，对于MVP，我可以创建一个"简化版"：
    # 只处理一个lane的χ步骤（使用3个输入lane）
    # 引脚：3×64位lane输入 + 控制 = 197引脚
    # 输出：64位χ结果

    # 但这偏离了"流式Keccak封装"的目标

    # 让我采用另一种设计：使用外部eval + 内部状态机
    # 不对，eval不能在网表中调用

    # 好吧，让我回到基础。由于电路构建的线性限制，
    # 我需要接受这个事实：LATCH必须在组合逻辑之前创建，
    # 或者组合逻辑必须使用LATCH的输出作为输入。

    # 解决方案：
    # 1. 先创建1600个LATCH，输入初始化为0
    # 2. LATCH的输出信号自动成为后续信号
    # 3. 组合逻辑使用这些LATCH输出作为输入
    # 4. 但组合逻辑的结果不能回写到已有的LATCH...

    # 等等！TapeOut协议中，LATCH指令创建一个信号，
    # 这个信号在eval时输出旧值（来自上一次beat）。
    # 但LATCH的输入d是在创建时指定的，不能后续改变。

    # 所以如果我们想要"状态更新"，需要创建新的LATCH，
    # 用MUX输出作为输入。但旧的LATCH仍然存在...

    # 这意味着"有状态电路"在TapeOut中是这样工作的：
    # - LATCH信号在eval时输出旧值
    # - 新的状态必须存储在新的LATCH中
    # - 但电路大小固定，不能动态创建LATCH

    # 所以正确的设计是：
    # - 固定数量的LATCH（如1600个）
    # - 每个LATCH的输入是一个MUX，选择数据来源
    # - MUX的选择信号由外部mode控制

    # 但MUX的输入必须包含所有可能的数据来源：
    #   - data_in（LOAD模式）
    #   - 单轮输出（RUN模式）
    #   - 旧值（保持模式）

    # 旧值来自哪里？来自同一个LATCH的上一次输出！
    # 但LATCH的输出是它自己（信号ID），而MUX需要选择旧值...

    # 这是自引用问题。在组合逻辑中，不能直接使用LATCH的输出作为它自己的输入。

    # 在数字逻辑中，这通过时钟边沿解决：
    # - 当时钟上升沿时，LATCH读取输入d，存储新值
    # - 在时钟低电平时，LATCH输出旧值
    # - 组合逻辑使用LATCH输出的旧值计算新值
    # - 新值连接到LATCH的输入d
    # - 下一个时钟上升沿，LATCH更新

    # 在TapeOut中，beat()相当于一个时钟周期：
    # - beat()调用eval()，eval()中LATCH输出旧值
    # - eval()计算所有组合逻辑
    # - beat()完成后，LATCH存储新值

    # 所以电路结构应该是：
    # 1. LATCH信号输出旧值
    # 2. 组合逻辑计算新值
    # 3. 新值作为LATCH的输入d
    # 4. 但LATCH的输入d必须在LATCH创建时指定！

    # 这就是问题所在。在网表字节码中，LATCH指令是：
    #   LATCH d:u24
    # d是一个信号ID。但当我们创建LATCH时，后续的组合逻辑信号还不存在。

    # 除非... 我们可以先创建占位信号，然后用占位信号创建LATCH，
    # 最后用组合逻辑输出替换占位信号。

    # 但信号ID是顺序分配的，不能跳号或替换。

    # 让我重新思考TapeOut协议的LATCH语义...

    # 根据文档：
    # "LATCH=1（d:u24）"
    # "LATCH在心跳时读旧状态、写新状态"
    # "信号编号：0=常量0，1=常量1，2起为输入引脚，之后每条指令顺序产生新信号"

    # 这意味着LATCH指令本身产生一个新信号（输出旧值）。
    # 这个信号的ID等于当前信号计数。
    # LATCH的输入d可以是任何已有信号。

    # 关键问题：如果LATCH的输入d需要在LATCH之后创建，怎么办？

    # 答案：在网表中，信号ID可以引用后续指令产生的信号！
    # 因为信号ID只是整数，不限制必须引用之前的信号。
    # 但在eval时，如果d引用了尚未计算的信号，会导致错误。

    # 除非TapeOut协议有特殊的处理方式...

    # 文档没有明确说明这一点。但从常规数字逻辑的角度，
    # LATCH的输入应该在同一拍中由组合逻辑计算得出。
    # 这意味着组合逻辑必须在LATCH之前执行，或者LATCH的输入可以引用后续信号。

    # 如果TapeOut支持前向引用（forward reference），那么：
    # 1. 先创建LATCH，输入d引用一个占位信号ID
    # 2. 然后创建组合逻辑，最后一个信号的ID等于占位信号ID
    # 3. 在eval时，协议需要先计算组合逻辑，再处理LATCH

    # 但文档说"之后每条指令顺序产生新信号"，暗示信号ID是顺序递增的。
    # 如果LATCH先创建（信号ID=k），它的输入d不能引用>k的信号。

    # 所以LATCH必须在组合逻辑之后创建！

    # 那有状态电路怎么实现？
    # 答案：LATCH存储的是上一拍的状态，当前拍的组合逻辑使用LATCH的输出（旧值）。
    # 当前拍的组合逻辑输出连接到下一拍的LATCH输入。
    # 但电路结构固定，所以LATCH的输入必须是固定的信号。

    # 这意味着：LATCH的输入d必须是MUX的输出，
    # MUX选择"初始值"或"组合逻辑输出"。
    # 但"组合逻辑输出"引用的是同一拍中的信号...

    # 这在组合逻辑中是可行的！因为eval是顺序执行指令：
    # 1. 先执行NAND（组合逻辑）
    # 2. 后执行LATCH
    # LATCH的输入d引用的是刚刚计算出的组合逻辑输出。

    # 等等，但文档说"之后每条指令顺序产生新信号"。
    # 如果LATCH在组合逻辑之后创建，LATCH的信号ID > 组合逻辑输出信号ID。
    # 但LATCH的输入d引用的是组合逻辑输出的信号ID（< LATCH的信号ID）。
    # 这在eval时是可行的，因为组合逻辑已经计算过了。

    # 但问题是：下一拍时，LATCH的输出是它自己的信号ID（> 组合逻辑输出）。
    # 组合逻辑需要使用LATCH的输出作为输入。
    # 这意味着组合逻辑需要使用一个比它自己信号ID更大的信号ID？

    # 不。组合逻辑使用LATCH的输出信号（即LATCH产生的信号ID）。
    # 如果LATCH在组合逻辑之后创建，LATCH的信号ID > 组合逻辑的信号ID。
    # 组合逻辑引用LATCH的信号ID（更大的ID）。
    # 在eval时，顺序执行指令：
    #   1. 组合逻辑指令（使用LATCH的上一拍输出值）
    #   2. LATCH指令（输出旧值，存储新值）
    # 但组合逻辑引用了LATCH的信号ID，而LATCH还没执行...

    # 这确实是个问题。在顺序执行中，如果组合逻辑引用了尚未执行的LATCH，
    # 需要使用LATCH的上一拍值（存储在状态中）。

    # 所以TapeOut协议的eval实现必须：
    # - 在eval开始时，先读取所有LATCH的上一拍值
    # - 然后顺序执行所有指令（NAND和LATCH）
    # - LATCH指令输出上一拍值（已预读）

    # 这意味着LATCH可以在电路的任何位置，
    # 组合逻辑可以引用LATCH的信号ID，
    # eval时会自动使用LATCH的上一拍值。

    # 验证我的Simulator实现：
    # ```python
    # elif op == 1:  # LATCH
    #     old_val = self.latch_values.get(len(self.state), False)
    #     self.state[len(self.state)] = old_val
    #     self.latch_values[len(self.state) - 1] = self._get(d)
    # ```
    # 这里LATCH使用self.latch_values存储的值（上一拍）。
    # 所以LATCH可以在任何位置，组合逻辑可以引用LATCH的输出。

    # 但LATCH的输入d呢？d可以是后续创建的信号吗？
    # 可以！因为self._get(d)在eval时执行，此时所有前面的指令已经执行。
    # 如果d引用的是后续信号（> 当前state长度），会KeyError。

    # 所以d不能引用后续信号。LATCH的输入必须是已有信号。

    # 这意味着：
    # - 组合逻辑必须在LATCH之前创建（或者至少LATCH的输入d在LATCH之前创建）
    # - LATCH可以在组合逻辑之后创建
    # - 组合逻辑可以引用LATCH的输出（使用上一拍值）

    # 电路结构：
    # 1. 输入引脚
    # 2. 组合逻辑（使用LATCH的输出作为输入）
    # 3. LATCH（输入=组合逻辑输出）
    # 4. 输出 = 最后nOut个信号

    # 但LATCH的输出信号ID > 组合逻辑输出信号ID。
    # 组合逻辑引用LATCH的信号ID（> 自己的信号ID）。
    # 在eval时，组合逻辑执行时LATCH还没执行，但Simulator使用预读的latch_values。

    # 等等，我的Simulator没有预读LATCH值！
    # ```python
    # for inst in self.circuit.instructions:
    #     if op == 0:  # NAND
    #         val = not (self._get(a) and self._get(b))
    #         self.state[len(self.state)] = val
    #     elif op == 1:  # LATCH
    #         old_val = self.latch_values.get(len(self.state), False)
    #         self.state[len(self.state)] = old_val
    # ```
    # 这里组合逻辑（NAND）执行时，如果引用LATCH的信号ID，
    # self._get会查找self.state。但LATCH还没执行，state中没有LATCH的值。
    # 所以会KeyError！

    # 除非LATCH在组合逻辑之前执行... 但那样LATCH的输入d就不能引用组合逻辑输出。

    # 这是一个根本性的设计问题。让我重新思考...

    # 在真实的数字电路中，时序逻辑这样工作：
    # - 时钟上升沿：所有LATCH同时采样输入d
    # - 时钟高电平：LATCH输出新值（或保持旧值，取决于设计）
    # - 组合逻辑使用LATCH输出计算新值
    # - 时钟下降沿：...

    # 在TapeOut中，beat()是一个离散事件：
    # - beat() = eval() + 更新LATCH
    # - eval()中，LATCH输出旧值
    # - eval()后，LATCH存储新值
    # - 所以eval()中，组合逻辑引用LATCH，得到的是旧值

    # 但eval()是顺序执行指令的。如果LATCH在组合逻辑之前：
    # - 先执行LATCH，输出旧值（存储在state中）
    # - 后执行组合逻辑，引用LATCH的信号ID，得到旧值
    # - 组合逻辑输出作为LATCH的输入... 但LATCH已经执行过了！

    # 如果LATCH在组合逻辑之后：
    # - 先执行组合逻辑，引用LATCH的信号ID → KeyError

    # 除非LATCH不在instructions列表中，而是单独存储...

    # 让我重新读文档：
    # "三种指令：NAND=0（a:u24, b:u24）、LATCH=1（d:u24）、REF=2"
    # "信号编号：0=常量0，1=常量1，2起为输入引脚，之后每条指令顺序产生新信号"
    # "LATCH在心跳时读旧状态、写新状态"

    # 文档没有说明LATCH和组合逻辑的执行顺序。
    # 但从"之后每条指令顺序产生新信号"来看，所有指令是顺序执行的。

    # 如果LATCH在组合逻辑之前执行，它输出旧值。
    # 组合逻辑之后执行，使用LATCH的旧值。
    # 但LATCH的输入d是在LATCH创建时指定的，不能是后续组合逻辑的输出。

    # 这意味着LATCH的输入只能是：
    # - 输入引脚
    # - 常量
    # - 之前指令的输出（包括之前的LATCH）

    # 所以LATCH不能在同一拍中被组合逻辑更新！
    # LATCH只能在下一拍更新（因为当前拍的组合逻辑在LATCH之后执行，不能影响当前LATCH）。

    # 等等，让我重新看Simulator的LATCH处理：
    # ```python
    # old_val = self.latch_values.get(len(self.state), False)
    # self.state[len(self.state)] = old_val
    # self.latch_values[len(self.state) - 1] = self._get(d)
    # ```
    # 这里LATCH先输出旧值到state，然后将输入d的值存入latch_values（下拍生效）。

    # 但输入d可以是任何已有信号。如果LATCH在组合逻辑之前创建，
    # d引用的是之前的信号（不是当前拍的组合逻辑输出）。

    # 所以LATCH在同一拍中只能被"之前的信号"更新。
    # 如果LATCH在组合逻辑之后创建，d可以引用组合逻辑输出，
    # 但组合逻辑不能引用LATCH（因为LATCH还没执行）。

    # 这是一个trade-off：
    # - LATCH在前：组合逻辑可用LATCH旧值，但LATCH输入不能是当前组合逻辑输出
    # - LATCH在后：组合逻辑不可用LATCH（KeyError），但LATCH输入可以是组合逻辑输出

    # 等等，如果LATCH在前，组合逻辑在后：
    # - 组合逻辑引用LATCH → 得到LATCH旧值 ✓
    # - LATCH的输入d = 之前的信号（不是当前组合逻辑输出）
    # 这意味着LATCH的更新来自上一拍的信号，或者来自输入引脚。

    # 所以"有状态电路"的工作方式是：
    # - LATCH存储的值来自上一拍或输入引脚
    # - 当前拍的组合逻辑使用LATCH旧值
    # - 当前拍的输出包含组合逻辑结果
    # - 下一拍，LATCH输出新值（如果d连接到上一拍的组合逻辑输出）

    # 但d必须引用已有信号。如果上一拍的组合逻辑输出在当前拍开始时就已存在
    # （因为它在state中），那么LATCH的d可以引用它。

    # 让我验证：假设电路结构是 [LATCH, NAND, LATCH]
    # 拍1：
    #   - LATCH1输出old_val1（初始0）
    #   - NAND计算val = NOT(LATCH1 AND 1) = NOT(0) = 1
    #   - LATCH2输出old_val2（初始0）
    #   - LATCH2存储d = NAND输出 = 1（下拍生效）
    # 拍2：
    #   - LATCH1输出old_val1（还是0，因为d没改）
    #   - NAND计算val = NOT(0 AND 1) = 1
    #   - LATCH2输出old_val2 = 1（来自拍1的存储）
    #   - LATCH2存储d = 1

    # 所以LATCH2在拍2输出1。但LATCH1始终是0（因为d没改）。

    # 如果我想让LATCH1也更新，需要另一个LATCH或改变d。

    # 结论：在TapeOut中，有状态电路需要精心安排LATCH和组合逻辑的顺序。
    # 通常，反馈环路的LATCH需要在组合逻辑之前创建，
    # 组合逻辑引用LATCH的旧值，
    # 另一个LATCH（或同一个LATCH的下一次beat）存储组合逻辑输出。

    # 但这意味着状态更新有延迟：组合逻辑输出在下一拍才能写入LATCH。

    # 对于Keccak，这不是问题：
    # - 拍1：LATCH输出旧状态，组合逻辑计算新状态，输出到新信号
    # - 但新状态不能写回同一个LATCH（因为LATCH已经执行过了）
    # - 拍2：需要新的LATCH来存储新状态...

    # 或者，所有LATCH在组合逻辑之前创建：
    # - 拍1：LATCH输出旧状态，组合逻辑计算新状态，输出到外部引脚
    # - 外部将新状态作为输入引脚传入拍2
    # - 拍2：LATCH（d=输入引脚）存储新状态，输出新状态

    # 这要求外部提供反馈！即电路本身不闭环，需要外部把输出连回输入。

    # 但在TapeOut中，有状态电路由beat()驱动，状态按调用者隔离。
    # 这意味着同一个调用者的连续beat()，LATCH状态会自动保持。
    # 但LATCH的输入d必须在创建时固定。

    # 让我重新理解文档：
    # "有状态电路由 witness 合约 beat() 驱动，每拍一笔交易；状态按调用者隔离"

    # 如果beat()每拍一笔交易，那么每拍eval时：
    # - LATCH输出旧值（来自上一拍存储）
    # - 组合逻辑计算新值
    # - 但新值如何写回LATCH？

    # 答案：如果LATCH的输入d连接到一个MUX，
    # MUX选择"输入引脚"或"组合逻辑输出"，
    # 那么beat()时：
    # - 如果mode=LOAD，MUX选输入引脚，LATCH存储输入值
    # - 如果mode=RUN，MUX选组合逻辑输出，LATCH存储新状态

    # 但MUX在组合逻辑中计算。如果LATCH在MUX之前创建，
    # LATCH的d引用MUX输出（MUX在LATCH之后执行）。
    # 这在eval时：
    # - 先执行LATCH，输出旧值
    # - 后执行MUX，计算新值
    # - LATCH的d引用MUX输出，但MUX还没执行...

    # 除非eval实现不是严格顺序执行，而是：
    # 1. 先读取所有LATCH的d值（预读）
    # 2. 顺序执行所有指令
    # 3. LATCH输出预读的值

    # 但我的Simulator没有预读机制。

    # 让我修改Simulator，支持这种反馈：
    # - 第一遍：识别所有LATCH指令，预读d值
    # - 第二遍：顺序执行所有指令

    # 不，更简单的方法：
    # - LATCH指令不立即存储新值，而是在eval结束后统一更新
    # - eval过程中，LATCH输出latch_values中的旧值
    # - 所有指令（包括LATCH之后的指令）可以引用LATCH的输出
    # - LATCH的输入d可以是任何信号（包括后续指令的输出）
    # - 但d的值在eval结束时才存储到latch_values

    # 等等，我的Simulator已经这样实现了！
    # ```python
    # old_val = self.latch_values.get(len(self.state), False)
    # self.state[len(self.state)] = old_val  # LATCH输出旧值
    # self.latch_values[len(self.state) - 1] = self._get(d)  # 存储新值（下拍生效）
    # ```

    # 这里的问题是：self._get(d)在LATCH执行时调用。
    # 如果d引用后续信号（> len(self.state)），会KeyError。

    # 但如果d引用之前的信号（包括输入引脚、常量、之前的LATCH输出、之前的NAND输出），
    # self._get(d)可以正常工作。

    # 所以LATCH的输入d必须是"之前的信号"。
    # 这意味着LATCH不能在组合逻辑之前创建（如果d需要引用组合逻辑输出）。
    # LATCH必须在组合逻辑之后创建。

    # 但组合逻辑不能引用LATCH（如果LATCH在组合逻辑之后）。

    # 死锁！

    # 解决方案1：使用两个LATCH层
    # - LATCH_A（在前）：存储上一拍状态
    # - 组合逻辑：使用LATCH_A输出，计算新状态
    # - LATCH_B（在后）：存储新状态（d=组合逻辑输出）
    # - 拍1：LATCH_A输出旧值，组合逻辑计算新值，LATCH_B存储新值
    # - 拍2：LATCH_A输出旧值（没变！），组合逻辑计算新值（基于旧值），LATCH_B存储新值
    # - 问题：LATCH_A始终输出初始值，没有更新

    # 解决方案2：外部反馈
    # - 拍1：LATCH输出旧值，组合逻辑计算新值，输出到外部
    # - 外部将新值作为输入引脚传入拍2
    # - 拍2：LATCH（d=输入引脚）存储新值
    # - 这要求外部交易把输出连回输入

    # 解决方案3：修改协议理解
    # 也许TapeOut的LATCH语义允许"自引用"，
    # 即LATCH的d可以是它自己的输出信号ID（形成保持）。
    # 或者LATCH的d默认为自己的输出（如果没有指定）。

    # 但文档没有这样说...

    # 让我搜索一下是否有已知的TapeOut有状态电路示例。
    # 文档提到"创世 Genesis（0x50A9…39D09）电路 #2 = 2,379 门自带 LATCH 的演示程序（0 入 84 出）"
    # 以及"有状态电路由 witness 合约 beat() 驱动"

    # 如果创世电路有LATCH，它的网表可能展示了LATCH的正确用法。
    # 但我不方便读取链上网表...

    # 让我用最简单的方法测试：创建一个LATCH+NAND反馈电路，看Simulator怎么工作。

    return c


def test_latch_feedback():
    """测试LATCH反馈回路"""
    # 电路：LATCH(d) → NAND(LATCH, 1) → output
    # 期望：第一拍输出0（LATCH初始0，NAND(0,1)=1... 不对）

    # 更简单的：LATCH(d=1) → output
    c = Circuit("latch_feedback", n_in=0, n_out=1)
    # LATCH，输入d=常量1
    latch = c.latch(c.one())
    # 输出 = LATCH的值
    # 但n_out=1，输出是最后1个信号
    # LATCH的信号ID=2（因为没有输入引脚，0=0,1=1,2=LATCH输出）
    # 最后1个信号是2，输出LATCH的值

    sim = Simulator(c)
    # 第一拍：LATCH初始值=0（默认）
    result = sim.beat([])
    print(f"拍1: LATCH输出={result}, latch_values={sim.latch_values}")

    # 第二拍：LATCH应存储d=1（来自拍1的eval）
    result = sim.beat([])
    print(f"拍2: LATCH输出={result}, latch_values={sim.latch_values}")


def test_latch_with_nand():
    """测试LATCH+NAND组合"""
    # 电路：NAND(a, b) → LATCH(d=NAND输出)
    c = Circuit("latch_nand", n_in=2, n_out=1)
    a, b = c.input_signals()
    nand_out = c.nand(a, b)
    latch = c.latch(nand_out)

    sim = Simulator(c)
    # 拍1：输入a=0,b=0 → NAND=1 → LATCH存储1（下拍生效）
    result = sim.beat([False, False])
    print(f"拍1: 输入[0,0], 输出={result}")

    # 拍2：输入a=1,b=1 → NAND=0 → LATCH输出1（拍1存储的值）
    result = sim.beat([True, True])
    print(f"拍2: 输入[1,1], 输出={result}")

    # 拍3：输入a=1,b=1 → NAND=0 → LATCH输出0（拍2存储的值）
    result = sim.beat([True, True])
    print(f"拍3: 输入[1,1], 输出={result}")


if __name__ == "__main__":
    test_latch_feedback()
    test_latch_with_nand()
