<?xml version="1.0" encoding="utf-8"?>
<!-- Date(ymd)/time(hms) 2026/09/29 17:30:00 -->
<!-- ================================================================
     程序号 : 200  ← 控制器上唯一常驻服务程序（三合一）
     ① 点动     40135.Bit0 → MJOINT，完成位 40035.Bit0
     ② 吸气     40135.Bit1 → io.DOut[8]=true  **电平保持**（已取消 0.5s 自动停），完成位 40035.Bit1
     ③ 停止吸气 40135.Bit2 → io.DOut[8]=false，完成位 40035.Bit2
     ★ N=8 是参考项目 CARRY.XPL 的接法 → 现场以 IO 监控实测为准，改 ②③ 两处 io.DOut[N]
     ★ 循环顶部三条"清完成位"不能删：防残留 1 被 PC 误判"已完成"→ 分支永不执行
     ================================================================ -->
<Xpl-source>
<HostEnvironment>
<Config>
<FileId>0x200a2b01</FileId>
<Target>Robox Motion Control</Target>
<XplType>RoboticProgramLanguage</XplType>
</Config>
</HostEnvironment>
<XplEnvironment>
<Title LcId="1033">
<![CDATA[200 三合一常驻服务(点动+吸气+停止吸气)]]>
</Title>
<Description LcId="1033">
<![CDATA[常驻服务：40135.Bit0=点动 Bit1=吸气(保持) Bit2=停止吸气。完成位 40035.Bit0/1/2。★ DOut[N] 的 N 以现场 IO 实测为准（当前 8）。]]>
</Description>
<Version>2.0.0</Version>
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
<text>调度层：每轮先把三条完成位清 0（防残留被 PC 误判），再并行门控 点动/吸气/停止吸气</text>
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
<dwell>
<expr>0.02</expr>
</dwell>
</Body>
<Pou>
<Name>2</Name>
<Body>
<rem>
<text>① 点动：读 40139~44 目标绝对角(×100) → MJOINT，完成位置 40035.Bit0</text>
</rem>
<mjoint>
<target>POINTJ(fidbus.mtcp_ro_i[0]/100.0,fidbus.mtcp_ro_i[1]/100.0,fidbus.mtcp_ro_i[2]/100.0,fidbus.mtcp_ro_i[3]/100.0,fidbus.mtcp_ro_i[4]/100.0,fidbus.mtcp_ro_i[5]/100.0)</target>
<speed>v50perc</speed>
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
<text>② 吸气：DOut[N]=true 并**保持**（不自动停），完成位置 40035.Bit1；★ N 以现场实测为准，当前 8</text>
</rem>
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
<text>③ 停止吸气：DOut[N]=false 立即关断真空，完成位置 40035.Bit2；★ N 同 ②</text>
</rem>
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
</Pou>
</Pou>
</Pous>
</XplEnvironment>
</Xpl-source>
