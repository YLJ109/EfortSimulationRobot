<?xml version="1.0" encoding="utf-8"?>
<!-- Date(ymd)/time(hms) 2026/09/29 20:10:00 -->
<!-- ================================================================
     程序号 : 210  ——  控制器上唯一常驻服务程序（点动 + 四路气路）  v4.0
     ① 点动    40135.Bit0 → MJOINT(**v100perc**)  完成位 40035.Bit0
     ② 吸      40135.Bit1 → io.DOut[N]=true      完成位 40035.Bit1
     ③ 停止吸  40135.Bit2 → io.DOut[N]=false     完成位 40035.Bit2
     ④ 放      40135.Bit3 → io.DOut[M]=true      完成位 40035.Bit3
     ⑤ 停止放  40135.Bit4 → io.DOut[M]=false     完成位 40035.Bit4
     ★★ 速度常量必须是 v100perc：有效速度 = v…perc × 40103。用 v50perc 时上位机要
        把 40103 写成本身 2 倍（设 17% 写 34%）→ 示教器显示 34%、右上角 17%。
        改 v100perc 后**写多少就是多少**，三者一致（config service_program_vperc 同步 = 100）。
     ★ N=真空阀号（参考 8）；M=吹气阀号（参考 9）——以现场 IO 监控实测为准。
     ★ 四路气路全部**电平保持**：吸/放 开了就一直保持，靠 ③/⑤ 关。
     ★ 完成位「循环顶 + 块内」两处都清 0：防残留 1 被上位机误判"已完成"。
     ★ 本文件是 .XPL（结构化 XML）；<condition>!xxx</condition> 是示教器导出的规范化写法。
        ★ 若在示教器里**敲文本源码**，取反必须写 NOT，不要写 !（见 docs 规范 铁律 4）。
     ================================================================ -->
<Xpl-source>
<HostEnvironment>
<Config>
<FileId>0x210a2b04</FileId>
<Target>Robox Motion Control</Target>
<XplType>RoboticProgramLanguage</XplType>
</Config>
</HostEnvironment>
<XplEnvironment>
<Title LcId="1033">
<![CDATA[210 常驻服务(点动v100perc + 吸/停止吸/放/停止放)]]>
</Title>
<Description LcId="1033">
<![CDATA[40135.Bit0 点动 / Bit1 吸 / Bit2 停止吸 / Bit3 放 / Bit4 停止放；完成位 40035.Bit0~4。速度 v100perc（与上位机 40103 一致）。DOut[N] 真空、DOut[M] 吹气，输出号以现场 IO 实测为准（参考 8/9）。]]>
</Description>
<Version>4.0.0</Version>
<Author>FIT</Author>
<Pous>
<Pou>
<Name>Main</Name>
<Type>Program</Type>
<Body>
<while>
<cond>true</cond>
<pouref>1</pouref>
</while>
</Body>
<Pou>
<Name>1</Name>
<Body>
<rem>
<text>调度层：循环顶先清五条完成位（空闲期恒 0，上位机置触发后第一次轮询必读到 0），再并行门控 点动/吸/停止吸/放/停止放</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[0]</dest>
<expr>false</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[1]</dest>
<expr>false</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[2]</dest>
<expr>false</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[3]</dest>
<expr>false</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[4]</dest>
<expr>false</expr>
</set>
<while>
<cond>fidbus.mtcp_ro_b[0]</cond>
<pouref>2</pouref>
</while>
<while>
<cond>fidbus.mtcp_ro_b[1]</cond>
<pouref>3</pouref>
</while>
<while>
<cond>fidbus.mtcp_ro_b[2]</cond>
<pouref>4</pouref>
</while>
<while>
<cond>fidbus.mtcp_ro_b[3]</cond>
<pouref>5</pouref>
</while>
<while>
<cond>fidbus.mtcp_ro_b[4]</cond>
<pouref>6</pouref>
</while>
<dwell>
<expr>0.02</expr>
</dwell>
</Body>
<Pou>
<Name>2</Name>
<Body>
<rem>
<text>① 点动：清完成位 → 读 40139~44 目标绝对角(×100) → MJOINT(**v100perc**) → 置完成位 → 等上位机撤触发 → 清完成位</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[0]</dest>
<expr>false</expr>
</set>
<mjoint>
<target>POINTJ(fidbus.mtcp_ro_i[0]/100.0,fidbus.mtcp_ro_i[1]/100.0,fidbus.mtcp_ro_i[2]/100.0,fidbus.mtcp_ro_i[3]/100.0,fidbus.mtcp_ro_i[4]/100.0,fidbus.mtcp_ro_i[5]/100.0)</target>
<speed>v100perc</speed>
<zone>fine</zone>
<tool>tool0</tool>
</mjoint>
<set>
<dest>fidbus.mtcp_wo_b[0]</dest>
<expr>true</expr>
</set>
<wait>
<condition>!fidbus.mtcp_ro_b[0]</condition>
</wait>
<set>
<dest>fidbus.mtcp_wo_b[0]</dest>
<expr>false</expr>
</set>
</Body>
</Pou>
<Pou>
<Name>3</Name>
<Body>
<rem>
<text>② 吸：DOut[N]=true 并**保持**（不自动停），完成位 40035.Bit1；★ N=8 真空阀，以现场实测为准</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[1]</dest>
<expr>false</expr>
</set>
<set>
<dest>io.DOut[8]</dest>
<expr>true</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[1]</dest>
<expr>true</expr>
</set>
<wait>
<condition>!fidbus.mtcp_ro_b[1]</condition>
</wait>
<set>
<dest>fidbus.mtcp_wo_b[1]</dest>
<expr>false</expr>
</set>
</Body>
</Pou>
<Pou>
<Name>4</Name>
<Body>
<rem>
<text>③ 停止吸：DOut[N]=false 关真空阀，完成位 40035.Bit2；★ N 同 ②</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[2]</dest>
<expr>false</expr>
</set>
<set>
<dest>io.DOut[8]</dest>
<expr>false</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[2]</dest>
<expr>true</expr>
</set>
<wait>
<condition>!fidbus.mtcp_ro_b[2]</condition>
</wait>
<set>
<dest>fidbus.mtcp_wo_b[2]</dest>
<expr>false</expr>
</set>
</Body>
</Pou>
<Pou>
<Name>5</Name>
<Body>
<rem>
<text>④ 放：DOut[M]=true 开吹气并**保持**，完成位 40035.Bit3；★ M=9 吹气阀，以现场实测为准</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[3]</dest>
<expr>false</expr>
</set>
<set>
<dest>io.DOut[9]</dest>
<expr>true</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[3]</dest>
<expr>true</expr>
</set>
<wait>
<condition>!fidbus.mtcp_ro_b[3]</condition>
</wait>
<set>
<dest>fidbus.mtcp_wo_b[3]</dest>
<expr>false</expr>
</set>
</Body>
</Pou>
<Pou>
<Name>6</Name>
<Body>
<rem>
<text>⑤ 停止放：DOut[M]=false 关吹气阀，完成位 40035.Bit4；★ M 同 ④</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[4]</dest>
<expr>false</expr>
</set>
<set>
<dest>io.DOut[9]</dest>
<expr>false</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[4]</dest>
<expr>true</expr>
</set>
<wait>
<condition>!fidbus.mtcp_ro_b[4]</condition>
</wait>
<set>
<dest>fidbus.mtcp_wo_b[4]</dest>
<expr>false</expr>
</set>
</Body>
</Pou>
</Pou>
</Pou>
</Pous>
</XplEnvironment>
</Xpl-source>
