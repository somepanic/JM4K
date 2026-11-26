{% extends "base.xml.tpl" %}
{% block headers %}<defaultcontrol>101</defaultcontrol>{% endblock %}
{% block controls %}
{% include "includes/jellyfin_backdrop.xml.tpl" %}
<control type="label">
    <posx>124</posx>
    <posy>{{ vscale(14) }}</posy>
    <width>1000</width>
    <height>{{ vscale(44) }}</height>
    <font>font12</font>
    <aligny>center</aligny>
    <textcolor>FFD7DBE2</textcolor>
    <label>$INFO[Window.Property(server.name)]</label>
</control>
<!-- Sign in to continue: the whole screen, no panel (Jellyfin's own login page, its users and buttons spread out) -->
<control type="label">
    <posx>0</posx>
    <posy>{{ vscale(150) }}</posy>
    <width>1920</width>
    <height>{{ vscale(80) }}</height>
    <font>font32_title</font>
    <align>center</align>
    <aligny>center</aligny>
    <textcolor>FFFFFFFF</textcolor>
    <label>$INFO[Window.Property(label.heading)]</label>
</control>

<!-- the users, then Use Quick Connect, Manual Login and Switch server -->
<control type="group">
    <visible allowhiddenfocus="true">String.IsEmpty(Window.Property(mode))</visible>
    <control type="list" id="101">
        <posx>0</posx>
        <posy>{{ vscale(250) }}</posy>
        <width>1920</width>
        <height>{{ vscale(300) }}</height>
        <scrolltime>200</scrolltime>
        <onup>500</onup>
        <ondown>401</ondown>
        <orientation>horizontal</orientation>
        <animation effect="slide" start="0,0" end="820,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,1)">Conditional</animation>
        <animation effect="slide" start="0,0" end="680,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,2)">Conditional</animation>
        <animation effect="slide" start="0,0" end="540,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,3)">Conditional</animation>
        <animation effect="slide" start="0,0" end="400,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,4)">Conditional</animation>
        <animation effect="slide" start="0,0" end="260,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,5)">Conditional</animation>
        <animation effect="slide" start="0,0" end="120,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,6)">Conditional</animation>

        <itemlayout width="280">
            <control type="image">
                <posx>25</posx>
                <posy>{{ vscale(6) }}</posy>
                <width>230</width>
                <height>{{ vscale(230) }}</height>
                <texture colordiffuse="FF222B3B" border="10">script.plex/white-square-rounded.png</texture>
            </control>
            <control type="image">
                <visible>!String.IsEmpty(ListItem.Thumb)</visible>
                <posx>25</posx>
                <posy>{{ vscale(6) }}</posy>
                <width>230</width>
                <height>{{ vscale(230) }}</height>
                <aspectratio>scale</aspectratio>
                <texture diffuse="script.plex/white-square-rounded.png" background="true">$INFO[ListItem.Thumb]</texture>
            </control>
            <control type="image">
                <visible>String.IsEmpty(ListItem.Thumb)</visible>
                <posx>85</posx>
                <posy>{{ vscale(61) }}</posy>
                <width>110</width>
                <height>{{ vscale(110) }}</height>
                <texture colordiffuse="FF47505C">script.plex/jellyfin/avatar.png</texture>
            </control>
            <control type="label">
                <posx>0</posx>
                <posy>{{ vscale(248) }}</posy>
                <width>280</width>
                <height>{{ vscale(40) }}</height>
                <font>font12</font>
                <align>center</align>
                <textcolor>FFB8BDC6</textcolor>
                <label>$INFO[ListItem.Label]</label>
            </control>
        </itemlayout>

        <focusedlayout width="280">
            <control type="image">
                <posx>25</posx>
                <posy>{{ vscale(6) }}</posy>
                <width>230</width>
                <height>{{ vscale(230) }}</height>
                <texture colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texture>
            </control>
            <control type="image">
                <visible>!String.IsEmpty(ListItem.Thumb)</visible>
                <posx>25</posx>
                <posy>{{ vscale(6) }}</posy>
                <width>230</width>
                <height>{{ vscale(230) }}</height>
                <aspectratio>scale</aspectratio>
                <texture diffuse="script.plex/white-square-rounded.png" background="true">$INFO[ListItem.Thumb]</texture>
            </control>
            <control type="image">
                <visible>String.IsEmpty(ListItem.Thumb)</visible>
                <posx>85</posx>
                <posy>{{ vscale(61) }}</posy>
                <width>110</width>
                <height>{{ vscale(110) }}</height>
                <texture colordiffuse="FF8A93A0">script.plex/jellyfin/avatar.png</texture>
            </control>
            <control type="label">
                <posx>0</posx>
                <posy>{{ vscale(248) }}</posy>
                <width>280</width>
                <height>{{ vscale(40) }}</height>
                <font>font12</font>
                <align>center</align>
                <textcolor>FFFFFFFF</textcolor>
                <label>$INFO[ListItem.Label]</label>
            </control>
            <control type="image">
                <visible>Control.HasFocus(101)</visible>
                <posx>19</posx>
                <posy>0</posy>
                <width>242</width>
                <height>{{ vscale(242) }}</height>
                <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
            </control>
        </focusedlayout>
    </control>
    <control type="button" id="401">
        <posx>160</posx>
        <posy>{{ vscale(600) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <align>center</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF47505C" border="10">script.plex/white-outline-rounded.png</texturenofocus>
        <onup>101</onup>
        <ondown>402</ondown>
        <label>$INFO[Window.Property(label.quickconnect)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(401)</visible>
        <posx>156</posx>
        <posy>{{ vscale(596) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="button" id="402">
        <posx>160</posx>
        <posy>{{ vscale(688) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <align>center</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF47505C" border="10">script.plex/white-outline-rounded.png</texturenofocus>
        <onup>401</onup>
        <ondown>403</ondown>
        <label>$INFO[Window.Property(label.manual)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(402)</visible>
        <posx>156</posx>
        <posy>{{ vscale(684) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="button" id="403">
        <posx>160</posx>
        <posy>{{ vscale(776) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <align>center</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF47505C" border="10">script.plex/white-outline-rounded.png</texturenofocus>
        <onup>402</onup>
        <label>$INFO[Window.Property(label.switch)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(403)</visible>
        <posx>156</posx>
        <posy>{{ vscale(772) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
</control>

<!-- Manual Login, or a user not signed in here: User and Password, then a Quick Connect code below -->
<control type="group">
    <visible allowhiddenfocus="true">String.IsEqual(Window.Property(mode),form)</visible>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(250) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font12</font>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[Window.Property(label.username)]</label>
    </control>
    <control type="button" id="201">
        <posx>160</posx>
        <posy>{{ vscale(292) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <aligny>center</aligny>
        <textoffsetx>24</textoffsetx>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF414C60" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF384252" border="10">script.plex/white-square-rounded.png</texturenofocus>
        <ondown>202</ondown>
        <label>$INFO[Window.Property(username)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(201)</visible>
        <posx>156</posx>
        <posy>{{ vscale(288) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(384) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font12</font>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[Window.Property(label.password)]</label>
    </control>
    <control type="button" id="202">
        <posx>160</posx>
        <posy>{{ vscale(426) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <aligny>center</aligny>
        <textoffsetx>24</textoffsetx>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF414C60" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF384252" border="10">script.plex/white-square-rounded.png</texturenofocus>
        <onup>201</onup>
        <ondown>203</ondown>
        <label>$INFO[Window.Property(password)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(202)</visible>
        <posx>156</posx>
        <posy>{{ vscale(422) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(510) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font10</font>
        <textcolor>FFF87171</textcolor>
        <label>$INFO[Window.Property(error)]</label>
    </control>
    <control type="button" id="203">
        <posx>160</posx>
        <posy>{{ vscale(556) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <align>center</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF4F46E5" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF3D36B2" border="10">script.plex/white-square-rounded.png</texturenofocus>
        <onup>202</onup>
        <ondown>205</ondown>
        <label>$INFO[Window.Property(label.signin)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(203)</visible>
        <posx>156</posx>
        <posy>{{ vscale(552) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FFFFFFFF" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="button" id="205">
        <posx>160</posx>
        <posy>{{ vscale(644) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <align>center</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF47505C" border="10">script.plex/white-outline-rounded.png</texturenofocus>
        <onup>203</onup>
        <label>$INFO[Window.Property(label.cancel)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(205)</visible>
        <posx>156</posx>
        <posy>{{ vscale(640) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="group">
        <visible>!String.IsEmpty(Window.Property(qc.ready))</visible>
        <control type="image">
            <posx>160</posx>
            <posy>{{ vscale(744) }}</posy>
            <width>280</width>
            <height>{{ vscale(280) }}</height>
            <aspectratio aligny="top">keep</aspectratio>
            <texture background="true">$INFO[Window.Property(qr)]</texture>
        </control>
    <control type="label">
        <posx>480</posx>
        <posy>{{ vscale(760) }}</posy>
        <width>1280</width>
        <height>{{ vscale(44) }}</height>
        <font>font13</font>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[Window.Property(label.qc.heading)]</label>
    </control>
    <control type="label">
        <posx>480</posx>
        <posy>{{ vscale(810) }}</posy>
        <width>1280</width>
        <height>{{ vscale(80) }}</height>
        <font>font45_title</font>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[Window.Property(pin)]</label>
    </control>
    <control type="label">
        <posx>480</posx>
        <posy>{{ vscale(900) }}</posy>
        <width>1280</width>
        <height>{{ vscale(90) }}</height>
        <font>font10</font>
        <textcolor>FF9AA3AF</textcolor>
        <wrapmultiline>true</wrapmultiline>
        <label>$INFO[Window.Property(instructions)]</label>
    </control>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(760) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font10</font>
        <textcolor>FF9AA3AF</textcolor>
        <label>$INFO[Window.Property(qc.error)]</label>
    </control>
</control>

<!-- Use Quick Connect: the code and its QR, then Cancel -->
<control type="group">
    <visible allowhiddenfocus="true">String.IsEqual(Window.Property(mode),quickconnect)</visible>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(250) }}</posy>
        <width>1600</width>
        <height>{{ vscale(44) }}</height>
        <font>font13</font>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[Window.Property(label.qc.heading)]</label>
    </control>
    <control type="image">
        <visible>!String.IsEmpty(Window.Property(qc.ready))</visible>
        <posx>160</posx>
        <posy>{{ vscale(310) }}</posy>
        <width>380</width>
        <height>{{ vscale(380) }}</height>
        <aspectratio aligny="top">keep</aspectratio>
        <texture background="true">$INFO[Window.Property(qr)]</texture>
    </control>
    <control type="label">
        <posx>600</posx>
        <posy>{{ vscale(330) }}</posy>
        <width>1160</width>
        <height>{{ vscale(90) }}</height>
        <font>font45_title</font>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[Window.Property(pin)]</label>
    </control>
    <control type="label">
        <posx>600</posx>
        <posy>{{ vscale(440) }}</posy>
        <width>1160</width>
        <height>{{ vscale(140) }}</height>
        <font>font12</font>
        <textcolor>FF9AA3AF</textcolor>
        <wrapmultiline>true</wrapmultiline>
        <label>$INFO[Window.Property(instructions)]</label>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(310) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font12</font>
        <textcolor>FF9AA3AF</textcolor>
        <label>$INFO[Window.Property(qc.error)]</label>
    </control>
    <control type="button" id="206">
        <posx>160</posx>
        <posy>{{ vscale(740) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <align>center</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF47505C" border="10">script.plex/white-outline-rounded.png</texturenofocus>
        <label>$INFO[Window.Property(label.cancel)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(206)</visible>
        <posx>156</posx>
        <posy>{{ vscale(736) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
</control>

<!-- power menu, in the top bar's right corner -->
<control type="group">
    <posx>1788</posx>
    <posy>{{ vscale(8) }}</posy>
    <control type="button" id="500">
        <posx>0</posx>
        <posy>0</posy>
        <width>72</width>
        <height>{{ vscale(56) }}</height>
        <ondown>101</ondown>
        <texturefocus colordiffuse="FF3D36B2" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus>-</texturenofocus>
        <label> </label>
    </control>
    <control type="image">
        <visible>!String.IsEmpty(Window.Property(dropdown))</visible>
        <posx>0</posx>
        <posy>0</posy>
        <width>72</width>
        <height>{{ vscale(56) }}</height>
        <texture colordiffuse="FF3D36B2" border="10">script.plex/white-square-rounded.png</texture>
    </control>
    <control type="image">
        <posx>18</posx>
        <posy>{{ vscale(10) }}</posy>
        <width>36</width>
        <height>{{ vscale(36) }}</height>
        <texture colordiffuse="FF8A93A0">script.plex/buttons/power.png</texture>
    </control>
</control>

<control type="group">
    <visible>!String.IsEmpty(Window.Property(busy))</visible>
    <animation effect="fade" start="0" end="100">Visible</animation>
    <control type="image">
        <posx>840</posx>
        <posy>{{ vperc(vscale(150)) }}</posy>
        <width>240</width>
        <height>{{ vscale(150) }}</height>
        <texture>script.plex/busy-back.png</texture>
        <colordiffuse>A0FFFFFF</colordiffuse>
    </control>
    <control type="image">
        <posx>915</posx>
        <posy>{{ vperc(vscale(38)) }}</posy>
        <width>90</width>
        <height>{{ vscale(38) }}</height>
        <texture diffuse="script.plex/busy-diffuse.png">script.plex/busy.gif</texture>
    </control>
</control>
{% endblock controls %}
