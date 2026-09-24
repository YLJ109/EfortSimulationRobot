<?xml version="1.0" encoding="utf-8"?>
<!-- Date(ymd)/time(hms) 2026/09/23 16:13:44 -->
<Xpl-source>
<HostEnvironment>
<Config>
<FileId>0x1ab53a1e</FileId>
<Target>Robox Motion Control</Target>
<XplType>RoboticProgramLanguage</XplType>
</Config>
</HostEnvironment>
<XplEnvironment>
<Info>
<Title LcId="1033">
<![CDATA[XPL program]]>
</Title>
<Description LcId="1033">
<![CDATA[XPL program edited from teachgun]]>
</Description>
<Version>1.0.0</Version>
<Author>Robox SpA</Author>
</Info>
<Pous>
<Pou>
<Name>Main</Name>
<Type>Program</Type>
<Body>
<rem>
<text>关闭吸真空输出 DOut[8]</text>
</rem>
<set>
<dest>io.DOut[8]</dest>
<expr>false</expr>
</set>
<mjoint>
<target>POINTC(372.73,303.88,163.12,-136.984851,85.601401,-170.217208,&quot;CFG2&quot;,0,0,0)</target>
<speed>v100perc</speed>
<zone>fine</zone>
<tool>tool0</tool>
</mjoint>
<set>
<dest>io.DOut[8]</dest>
<expr>true</expr>
</set>
<mjoint>
<target>POINTJ(40.78,-40.98,-33.12,8.05,69.91,-2.95)</target>
<speed>v100perc</speed>
<zone>fine</zone>
<tool>tool0</tool>
</mjoint>
<mjoint>
<target>POINTJ(80.67,-23.63,-60.35,-5.43,82.25,-1.75)</target>
<speed>v100perc</speed>
<zone>fine</zone>
<tool>tool0</tool>
</mjoint>
<mjoint>
<target>POINTJ(-56.16,-34.27,-37.00,-78.41,87.08,16.14)</target>
<speed>v100perc</speed>
<zone>fine</zone>
<tool>tool0</tool>
</mjoint>
<mjoint>
<target>POINTJ(-50.85,-67.21,-15.75,-78.52,90.43,6.88)</target>
<speed>v100perc</speed>
<zone>fine</zone>
<tool>tool0</tool>
</mjoint>
<mjoint>
<target>POINTJ(-50.85,-67.21,-15.75,-78.52,90.43,6.88)</target>
<speed>v100perc</speed>
<zone>fine</zone>
<tool>tool0</tool>
</mjoint>
<set>
<dest>io.DOut[8]</dest>
<expr>false</expr>
</set>
<rem>
<text />
</rem>
<rem>
<text />
</rem>
<rem>
<text />
</rem>
<rem>
<text />
</rem>
<rem>
<text />
</rem>
</Body>
</Pou>
</Pous>
</XplEnvironment>
</Xpl-source>