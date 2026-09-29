<?xml version="1.0" encoding="utf-8"?>
<!-- Date(ymd)/time(hms) 2026/09/29 16:50:00 -->
<Xpl-source>
<HostEnvironment>
<Config>
<FileId>0x2100a210</FileId>
<Target>Robox Motion Control</Target>
<XplType>RoboticProgramLanguage</XplType>
</Config>
</HostEnvironment>
<XplEnvironment>
<Title LcId="1033">
<![CDATA[210 吸气/放气服务]]>
</Title>
<Description LcId="1033">
<![CDATA[吸放常驻服务：40135.Bit1=吸气(保持) Bit2=放气(默认态,仅关断)。★ 注意：DOut[8] 是参考项目接法，本机电磁阀实际输出号以现场实测为准（改 <dest>io.DOut[N]</dest> 的 N）]]>
</Description>
<Version>1.1.0</Version>
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
<text>调度：并行门控 吸(Bit1)/放(Bit2)。吸气=输出保持 ON；放气=输出 OFF（默认态）</text>
</rem>
<set>
<dest>fidbus.mtcp_wo_b[1]</dest>
<expr>false</expr>
</set>
<set>
<dest>fidbus.mtcp_wo_b[2]</dest>
<expr>false</expr>
</set>
<while>
<cond>fidbus.mtcp_ro_b[1]</cond>
<pouref>2</pouref>
</while>
<while>
<cond>fidbus.mtcp_ro_b[2]</cond>
<pouref>3</pouref>
</while>
<dwell>
<expr>0.02</expr>
</dwell>
</Body>
<Pou>
<Name>2</Name>
<Body>
<rem>
<text>吸气：DOut[N]=true 并保持（直到放气）；★ N 以现场实测电磁阀为准，当前 8 为参考项目接法</text>
</rem>
<wait>
<condition>fidbus.mtcp_ro_b[1]</condition>
</wait>
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
</Body>
</Pou>
<Pou>
<Name>3</Name>
<Body>
<rem>
<text>放气（默认态）：DOut[N]=false 关真空，程序回到挂起等触发</text>
</rem>
<wait>
<condition>fidbus.mtcp_ro_b[2]</condition>
</wait>
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
</Body>
</Pou>
</Pou>
</Pou>
</Pous>
</XplEnvironment>
</Xpl-source>
