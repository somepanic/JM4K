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
    <label>$INFO[Window.Property(server.name)]</label>
</control>
<control type="label">
    <posx>0</posx>
    <posy>{{ vscale(140) }}</posy>
    <width>1920</width>
    <height>{{ vscale(80) }}</height>
    <font>font32_title</font>
    <align>center</align>
    <aligny>center</aligny>
    <textcolor>FFFFFFFF</textcolor>
    <label>$INFO[Window.Property(label.heading)]</label>
</control>
<control type="label">
    <posx>360</posx>
    <posy>{{ vscale(216) }}</posy>
    <width>1200</width>
    <height>{{ vscale(40) }}</height>
    <font>font10</font>
    <align>center</align>
    <textcolor>FF9AA3AF</textcolor>
    <label>$INFO[Window.Property(subtitle)]</label>
</control>
<control type="image">
    <posx>360</posx>
    <posy>{{ vscale(270) }}</posy>
    <width>1200</width>
    <height>{{ vscale(620) }}</height>
    <texture colordiffuse="FF1B2433" border="10">script.plex/white-square-rounded.png</texture>
</control>
<!-- the password form, as Jellyfin's Manual Login -->
<control type="group">
    <posx>420</posx>
    <posy>0</posy>
    <visible>!String.IsEmpty(Window.Property(password.enabled))</visible>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(310) }}</posy>
            <width>540</width>
            <height>{{ vscale(40) }}</height>
            <font>font10</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(label.username)]</label>
        </control>
        <control type="button" id="201">
            <posx>0</posx>
            <posy>{{ vscale(354) }}</posy>
            <width>540</width>
            <height>{{ vscale(72) }}</height>
            <font>font12</font>
            <aligny>center</aligny>
            <textoffsetx>22</textoffsetx>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FFFFFFFF</focusedcolor>
            <texturefocus colordiffuse="FF414C60" border="10">script.plex/white-square-rounded.png</texturefocus>
            <texturenofocus colordiffuse="FF384252" border="10">script.plex/white-square-rounded.png</texturenofocus>
            <ondown>202</ondown>
            <label>$INFO[Window.Property(username)]</label>
        </control>
        <control type="image">
            <visible>Control.HasFocus(201)</visible>
            <posx>-4</posx>
            <posy>{{ vscale(350) }}</posy>
            <width>548</width>
            <height>{{ vscale(80) }}</height>
            <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(446) }}</posy>
            <width>540</width>
            <height>{{ vscale(40) }}</height>
            <font>font10</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(label.password)]</label>
        </control>
        <control type="button" id="202">
            <posx>0</posx>
            <posy>{{ vscale(490) }}</posy>
            <width>540</width>
            <height>{{ vscale(72) }}</height>
            <font>font12</font>
            <aligny>center</aligny>
            <textoffsetx>22</textoffsetx>
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
            <posx>-4</posx>
            <posy>{{ vscale(486) }}</posy>
            <width>548</width>
            <height>{{ vscale(80) }}</height>
            <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(574) }}</posy>
            <width>540</width>
            <height>{{ vscale(40) }}</height>
            <font>font10</font>
            <textcolor>FFF87171</textcolor>
            <label>$INFO[Window.Property(error)]</label>
        </control>
        <control type="button" id="203">
            <posx>0</posx>
            <posy>{{ vscale(624) }}</posy>
            <width>540</width>
            <height>{{ vscale(76) }}</height>
            <font>font12</font>
            <align>center</align>
            <aligny>center</aligny>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FFFFFFFF</focusedcolor>
            <texturefocus colordiffuse="FF4F46E5" border="10">script.plex/white-square-rounded.png</texturefocus>
            <texturenofocus colordiffuse="FF3D36B2" border="10">script.plex/white-square-rounded.png</texturenofocus>
            <onup>202</onup>
            <ondown>204</ondown>
            <label>$INFO[Window.Property(label.signin)]</label>
        </control>
        <control type="image">
            <visible>Control.HasFocus(203)</visible>
            <posx>-4</posx>
            <posy>{{ vscale(620) }}</posy>
            <width>548</width>
            <height>{{ vscale(84) }}</height>
            <texture colordiffuse="FFFFFFFF" border="10">script.plex/white-outline-rounded.png</texture>
        </control>
</control>
<control type="image">
    <visible>!String.IsEmpty(Window.Property(password.enabled)) + !String.IsEmpty(Window.Property(quickconnect.enabled))</visible>
    <posx>1000</posx>
    <posy>{{ vscale(310) }}</posy>
    <width>1</width>
    <height>{{ vscale(540) }}</height>
    <texture colordiffuse="FF47505C">script.plex/white-square-1px.png</texture>
</control>
<!-- Quick Connect, shown at once beside the form: one button press less on a remote than Jellyfin's dialog -->
<control type="group">
    <visible>!String.IsEmpty(Window.Property(quickconnect.enabled))</visible>
    <posx>1040</posx>
    <posy>0</posy>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(310) }}</posy>
            <width>460</width>
            <height>{{ vscale(40) }}</height>
            <font>font13</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(label.phone)]</label>
        </control>
        <control type="image">
            <posx>0</posx>
            <posy>{{ vscale(366) }}</posy>
            <width>280</width>
            <height>{{ vscale(280) }}</height>
            <aspectratio aligny="top">keep</aspectratio>
            <texture background="true">$INFO[Window.Property(qr)]</texture>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(662) }}</posy>
            <width>460</width>
            <height>{{ vscale(80) }}</height>
            <font>font10</font>
            <textcolor>FF9AA3AF</textcolor>
            <wrapmultiline>true</wrapmultiline>
            <label>$INFO[Window.Property(instructions)]</label>
        </control>
        <control type="label">
            <posx>0</posx>
            <posy>{{ vscale(752) }}</posy>
            <width>460</width>
            <height>{{ vscale(70) }}</height>
            <font>font32_title</font>
            <textcolor>FFFFFFFF</textcolor>
            <label>$INFO[Window.Property(pin)]</label>
        </control>
</control>
<control type="label">
    <posx>360</posx>
    <posy>{{ vscale(920) }}</posy>
    <width>1000</width>
    <height>{{ vscale(60) }}</height>
    <font>font10</font>
    <aligny>center</aligny>
    <textcolor>FF9AA3AF</textcolor>
    <label>$INFO[Window.Property(label.server)]  [COLOR FFFFFFFF]$INFO[Window.Property(address)][/COLOR]</label>
</control>
<control type="button" id="204">
    <posx>1380</posx>
    <posy>{{ vscale(920) }}</posy>
    <width>180</width>
    <height>{{ vscale(60) }}</height>
    <font>font10</font>
    <align>center</align>
    <aligny>center</aligny>
    <textcolor>FFFFFFFF</textcolor>
    <focusedcolor>FFFFFFFF</focusedcolor>
    <texturefocus colordiffuse="FF3D36B2" border="10">script.plex/white-square-rounded.png</texturefocus>
    <texturenofocus colordiffuse="FF47505C" border="10">script.plex/white-outline-rounded.png</texturenofocus>
    <onup>203</onup>
    <label>$INFO[Window.Property(label.change)]</label>
</control>
{% endblock controls %}
