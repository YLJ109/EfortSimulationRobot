<?xml version="1.0" encoding="utf-8"?>
<!-- Date(ymd)/time(hms) 2026/09/29 18:00:00 -->
<!-- ================================================================
     程序号 : 210  ——  控制器上唯一常驻服务程序（三合一）  v2.3
     ① 点动     40135.Bit0 → MJOINT(v50perc)     完成位 40035.Bit0
     ② 吸气     40135.Bit1 → io.DOut[8]=true     **电平保持**（无 0.5s 自动停）
     ③ 停止吸气 40135.Bit2 → io.DOut[8]=false    完成位 40035.Bit2
     ★ N=8 是参考项目 CARRY.XPL 的接法 → 现场以 IO 监控实测为准
     ★ 完成位「循环顶 + 块内」两处都清 0：防残留 1 被上位机误判"已完成"
     ★ 本文件是 .XPL（结构化 XML）形态。里面的 <condition>!xxx</condition>
       是示教器导出的规范化写法；★ 若在示教器/IDE 里**敲文本源码**，
       取反必须写 NOT，不要写 !（见 docs/规范-RPL程序编写规范与200服务程序.md 铁律 4）。
     ================================================================ -->
<Xpl-source>
<HostEnvironment>
<Config>
<FileId>0x210a2b02</FileId>
<Target>Robox Motion Control</Target>
<XplType>RoboticProgramLanguage</XplType>
</Config>
</HostEnvironment>
<XplEnvironment>
<Title LcId="1033">
<![CDATA[210 三合一常驻服务(点动+吸气保持+停止吸气)]]>
</Title>
<Description LcId="1033">
<![CDATA[常驻服务：40135.Bit0=点动 Bit1=吸气(电平保持) Bit2=停止吸气。完成位 40035.Bit0/1/2。★ DOut[N] 的 N 以现场 IO 实测为准（当前 8）。]]>
</Description>
<Version>2.3.0</Version>
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
<text>调度层：循环顶先清三条完成位（保证空闲期恒 0 —— 上位机置触发后第一次轮询必读到 0，否则会把残留的 1 误判成"已完成"），再并行门控 点动/吸气/停止吸气</text>
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
<text>① 点动：清完成位 → 读 40139~44 目标绝对角(×100) → MJOINT → 置完成位 → 等上位机撤触发 → 清完成位</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[0]</dest>
<expr>false</expr>
</set>
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
<text>③ 停止吸气：DOut[N]=false 立即关断真空，完成位置 40035.Bit2；★ N 同 ②</text>
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
</Pou>
</Pou>
</Pous>
</XplEnvironment>
</Xpl-source>
