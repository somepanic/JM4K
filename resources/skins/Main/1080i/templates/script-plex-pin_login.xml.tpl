{% extends "base.xml.tpl" %}
{% block headers %}<defaultcontrol always="true">201</defaultcontrol>{% endblock %}
{% block controls %}
<control type="image">
    <posx>0</posx>
    <posy>0</posy>
    <width>1920</width>
    <height>1080</height>
    <texture colordiffuse="FF141417">script.plex/white-square.png</texture>
</control>
<!-- two soft spotlights behind everything: script.plex/glow.png is a white disc fading to transparent, dithered -->
<control type="image">
    <posx>-300</posx>
    <posy>{{ vscale(-420) }}</posy>
    <width>1200</width>
    <height>{{ vscale(1200) }}</height>
    <texture>script.plex/glow.png</texture>
</control>
<control type="image">
    <posx>1100</posx>
    <posy>{{ vscale(-520) }}</posy>
    <width>1200</width>
    <height>{{ vscale(1200) }}</height>
    <texture>script.plex/glow.png</texture>
</control>
<control type="group">
    <posx>140</posx>
    <posy>0</posy>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(80) }}</posy>
            <width>760</width>
            <height>{{ vscale(80) }}</height>
            <font>font45_title</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(server.name)]</label>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(160) }}</posy>
            <width>760</width>
            <height>{{ vscale(44) }}</height>
            <font>font12</font>
            <textcolor>FF9A9AA3</textcolor>
            <label>$INFO[Window.Property(subtitle)]</label>
        </control>
</control>
<control type="group">
    <posx>140</posx>
    <posy>0</posy>
    <visible>!String.IsEmpty(Window.Property(password.enabled))</visible>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(290) }}</posy>
            <width>760</width>
            <height>{{ vscale(50) }}</height>
            <font>font13</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(label.password.heading)]</label>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(370) }}</posy>
            <width>760</width>
            <height>{{ vscale(40) }}</height>
            <font>font10</font>
            <textcolor>FFD4D4D8</textcolor>
            <label>$INFO[Window.Property(label.username)]</label>
        </control>
        <control type="button" id="201">
            <posx>0</posx>
            <posy>{{ vscale(412) }}</posy>
            <width>760</width>
            <height>{{ vscale(84) }}</height>
            <font>font12</font>
            <aligny>center</aligny>
            <textoffsetx>26</textoffsetx>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FFFFFFFF</focusedcolor>
            <texturefocus colordiffuse="FFFFFFFF" border="10">script.plex/white-outline-rounded.png</texturefocus>
            <texturenofocus colordiffuse="40FFFFFF" border="10">script.plex/white-outline-rounded.png</texturenofocus>
            <ondown>202</ondown>
            <label>$INFO[Window.Property(username)]</label>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(522) }}</posy>
            <width>760</width>
            <height>{{ vscale(40) }}</height>
            <font>font10</font>
            <textcolor>FFD4D4D8</textcolor>
            <label>$INFO[Window.Property(label.password)]</label>
        </control>
        <control type="button" id="202">
            <posx>0</posx>
            <posy>{{ vscale(564) }}</posy>
            <width>760</width>
            <height>{{ vscale(84) }}</height>
            <font>font12</font>
            <aligny>center</aligny>
            <textoffsetx>26</textoffsetx>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FFFFFFFF</focusedcolor>
            <texturefocus colordiffuse="FFFFFFFF" border="10">script.plex/white-outline-rounded.png</texturefocus>
            <texturenofocus colordiffuse="40FFFFFF" border="10">script.plex/white-outline-rounded.png</texturenofocus>
            <onup>201</onup>
            <ondown>203</ondown>
            <label>$INFO[Window.Property(password)]</label>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(660) }}</posy>
            <width>760</width>
            <height>{{ vscale(40) }}</height>
            <font>font10</font>
            <textcolor>FFF87171</textcolor>
            <label>$INFO[Window.Property(error)]</label>
        </control>
        <control type="button" id="203">
            <posx>0</posx>
            <posy>{{ vscale(712) }}</posy>
            <width>760</width>
            <height>{{ vscale(84) }}</height>
            <font>font12</font>
            <align>center</align>
            <aligny>center</aligny>
            <textcolor>FF18181B</textcolor>
            <focusedcolor>FF000000</focusedcolor>
            <texturefocus colordiffuse="FFFFFFFF" border="10">script.plex/white-square-rounded.png</texturefocus>
            <texturenofocus colordiffuse="FFB4B4BB" border="10">script.plex/white-square-rounded.png</texturenofocus>
            <onup>202</onup>
            <ondown>204</ondown>
            <label>$INFO[Window.Property(label.signin)]</label>
        </control>
</control>
<control type="image">
    <posx>959</posx>
    <posy>{{ vscale(290) }}</posy>
    <width>2</width>
    <height>{{ vscale(600) }}</height>
    <texture colordiffuse="1FFFFFFF">script.plex/white-square.png</texture>
</control>
<control type="group">
    <posx>1060</posx>
    <posy>0</posy>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(290) }}</posy>
            <width>760</width>
            <height>{{ vscale(50) }}</height>
            <font>font13</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(label.phone)]</label>
        </control>
        <control type="image">
            <posx>0</posx>
            <posy>{{ vscale(360) }}</posy>
            <width>380</width>
            <height>{{ vscale(380) }}</height>
            <aspectratio aligny="top">keep</aspectratio>
            <texture background="true">$INFO[Window.Property(qr)]</texture>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(760) }}</posy>
            <width>760</width>
            <height>{{ vscale(84) }}</height>
            <font>font10</font>
            <textcolor>FFA1A1AA</textcolor>
            <wrapmultiline>true</wrapmultiline>
            <label>$INFO[Window.Property(instructions)]</label>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(846) }}</posy>
            <width>760</width>
            <height>{{ vscale(70) }}</height>
            <font>font32_title</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(pin)]</label>
        </control>
</control>
<control type="image">
    <posx>140</posx>
    <posy>{{ vscale(950) }}</posy>
    <width>1640</width>
    <height>1</height>
    <texture colordiffuse="1FFFFFFF">script.plex/white-square-1px.png</texture>
</control>
<control type="label">
    <posx>140</posx>
    <posy>{{ vscale(972) }}</posy>
    <width>1400</width>
    <height>{{ vscale(60) }}</height>
    <font>font10</font>
    <aligny>center</aligny>
    <textcolor>FFA1A1AA</textcolor>
    <label>$INFO[Window.Property(label.server)]  [COLOR FFFFFFFF]$INFO[Window.Property(address)][/COLOR]</label>
</control>
<control type="button" id="204">
    <posx>1600</posx>
    <posy>{{ vscale(972) }}</posy>
    <width>180</width>
    <height>{{ vscale(60) }}</height>
    <font>font10</font>
    <align>center</align>
    <aligny>center</aligny>
    <textcolor>FFD4D4D8</textcolor>
    <focusedcolor>FFFFFFFF</focusedcolor>
    <texturefocus colordiffuse="FFFFFFFF" border="10">script.plex/white-outline-rounded.png</texturefocus>
    <texturenofocus colordiffuse="40FFFFFF" border="10">script.plex/white-outline-rounded.png</texturenofocus>
    <onup>203</onup>
    <label>$INFO[Window.Property(label.change)]</label>
</control>
{% endblock controls %}
