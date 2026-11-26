{% extends "base.xml.tpl" %}
{% block headers %}<defaultcontrol always="true">201</defaultcontrol>{% endblock %}
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
    <label>Jellyfin</label>
</control>
<!-- Connect to Server: the whole screen, no panel (Jellyfin's own server page, left aligned) -->
<control type="group">
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(150) }}</posy>
        <width>1600</width>
        <height>{{ vscale(80) }}</height>
        <font>font32_title</font>
        <textcolor>FFFFFFFF</textcolor>
        <aligny>center</aligny>
        <label>$INFO[Window.Property(label.title)]</label>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(270) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font12</font>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[Window.Property(label.host)]</label>
    </control>
    <control type="button" id="201">
        <posx>160</posx>
        <posy>{{ vscale(314) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <aligny>center</aligny>
        <textoffsetx>24</textoffsetx>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF414C60" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF384252" border="10">script.plex/white-square-rounded.png</texturenofocus>
        <ondown>203</ondown>
        <label>$INFO[Window.Property(host)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(201)</visible>
        <posx>156</posx>
        <posy>{{ vscale(310) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(398) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font10</font>
        <textcolor>FF9AA3AF</textcolor>
        <label>$INFO[Window.Property(label.hint)]</label>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(440) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font10</font>
        <textcolor>FF8AB4F8</textcolor>
        <label>$INFO[Window.Property(found)]</label>
    </control>
    <control type="label">
        <posx>160</posx>
        <posy>{{ vscale(480) }}</posy>
        <width>1600</width>
        <height>{{ vscale(40) }}</height>
        <font>font10</font>
        <textcolor>FFF87171</textcolor>
        <label>$INFO[Window.Property(error)]</label>
    </control>
    <control type="button" id="203">
        <posx>160</posx>
        <posy>{{ vscale(540) }}</posy>
        <width>1600</width>
        <height>{{ vscale(76) }}</height>
        <font>font12</font>
        <align>center</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <focusedcolor>FFFFFFFF</focusedcolor>
        <texturefocus colordiffuse="FF4F46E5" border="10">script.plex/white-square-rounded.png</texturefocus>
        <texturenofocus colordiffuse="FF3D36B2" border="10">script.plex/white-square-rounded.png</texturenofocus>
        <onup>201</onup>
        <ondown>204</ondown>
        <label>$INFO[Window.Property(label.connect)]</label>
    </control>
    <control type="image">
        <visible>Control.HasFocus(203)</visible>
        <posx>156</posx>
        <posy>{{ vscale(536) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FFFFFFFF" border="10">script.plex/white-outline-rounded.png</texture>
    </control>
    <control type="button" id="204">
        <posx>160</posx>
        <posy>{{ vscale(628) }}</posy>
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
        <visible>Control.HasFocus(204)</visible>
        <posx>156</posx>
        <posy>{{ vscale(624) }}</posy>
        <width>1608</width>
        <height>{{ vscale(84) }}</height>
        <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
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
