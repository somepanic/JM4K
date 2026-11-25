{% extends "base.xml.tpl" %}
{% block headers %}<defaultcontrol>100</defaultcontrol>{% endblock %}
{% block controls %}
{% include "includes/jellyfin_backdrop.xml.tpl" %}
<control type="label">
    <posx>0</posx>
    <posy>{{ vscale(170) }}</posy>
    <width>1920</width>
    <height>{{ vscale(80) }}</height>
    <font>font32_title</font>
    <align>center</align>
    <aligny>center</aligny>
    <textcolor>FFFFFFFF</textcolor>
    <label>$ADDON[script.plexmod 35309]</label>
</control>
<control type="image">
    <posx>300</posx>
    <posy>{{ vscale(290) }}</posy>
    <width>1320</width>
    <height>{{ vscale(360) }}</height>
    <texture colordiffuse="FF1B2433" border="10">script.plex/white-square-rounded.png</texture>
</control>

<control type="group" id="100">
    <posx>0</posx>
    <posy>{{ vscale(340) }}</posy>
    <defaultcontrol always="true">101</defaultcontrol>

    <control type="list" id="101">
        <posx>0</posx>
        <posy>0</posy>
        <width>1920</width>
        <height>{{ vscale(260) }}</height>
        <scrolltime>200</scrolltime>
        <onup>500</onup>
        <orientation>horizontal</orientation>
        <animation effect="slide" start="0,0" end="840,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,1)">Conditional</animation>
        <animation effect="slide" start="0,0" end="720,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,2)">Conditional</animation>
        <animation effect="slide" start="0,0" end="600,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,3)">Conditional</animation>
        <animation effect="slide" start="0,0" end="480,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,4)">Conditional</animation>
        <animation effect="slide" start="0,0" end="360,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,5)">Conditional</animation>
        <animation effect="slide" start="0,0" end="240,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,6)">Conditional</animation>
        <animation effect="slide" start="0,0" end="120,0" time="0" condition="Integer.IsEqual(Container(101).NumItems,7)">Conditional</animation>

        <itemlayout width="240">
            <control type="group">
                <visible>!String.IsEmpty(ListItem.Property(empty))</visible>
                <control type="image">
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <texture colordiffuse="FF222B3B" border="10">script.plex/white-square-rounded.png</texture>
                </control>
                <control type="image">
                    <posx>80</posx>
                    <posy>{{ vscale(60) }}</posy>
                    <width>80</width>
                    <height>{{ vscale(80) }}</height>
                    <texture colordiffuse="FF8A93A0">script.plex/user_select/refresh.png</texture>
                </control>
            </control>
            <control type="group">
                <visible>String.IsEmpty(ListItem.Property(empty))</visible>
                <control type="image">
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <texture colordiffuse="FF222B3B" border="10">script.plex/white-square-rounded.png</texture>
                </control>
                <control type="image">
                    <visible>!String.IsEmpty(ListItem.Thumb)</visible>
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <aspectratio>scale</aspectratio>
                    <texture diffuse="script.plex/white-square-rounded.png" background="true">$INFO[ListItem.Thumb]</texture>
                </control>
                <control type="image">
                    <visible>String.IsEmpty(ListItem.Thumb) + !String.IsEmpty(ListItem.Label2)</visible>
                    <posx>70</posx>
                    <posy>{{ vscale(46) }}</posy>
                    <width>100</width>
                    <height>{{ vscale(100) }}</height>
                    <texture colordiffuse="FF47505C">script.plex/jellyfin/avatar.png</texture>
                </control>
                <control type="label">
                    <visible>String.IsEmpty(ListItem.Label2)</visible>
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <font>font45_title</font>
                    <align>center</align>
                    <aligny>center</aligny>
                    <textcolor>FF8A93A0</textcolor>
                    <label>+</label>
                </control>
                <control type="label">
                    <posx>0</posx>
                    <posy>{{ vscale(212) }}</posy>
                    <width>240</width>
                    <height>{{ vscale(40) }}</height>
                    <font>font10</font>
                    <align>center</align>
                    <textcolor>FFB8BDC6</textcolor>
                    <label>$INFO[ListItem.Label]</label>
                </control>
            </control>
        </itemlayout>

        <focusedlayout width="240">
            <control type="group">
                <visible>!String.IsEmpty(ListItem.Property(empty))</visible>
                <control type="image">
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <texture colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texture>
                </control>
                <control type="image">
                    <posx>80</posx>
                    <posy>{{ vscale(60) }}</posy>
                    <width>80</width>
                    <height>{{ vscale(80) }}</height>
                    <texture colordiffuse="FFFFFFFF">script.plex/user_select/refresh.png</texture>
                </control>
            </control>
            <control type="group">
                <visible>String.IsEmpty(ListItem.Property(empty))</visible>
                <control type="image">
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <texture colordiffuse="FF2A3549" border="10">script.plex/white-square-rounded.png</texture>
                </control>
                <control type="image">
                    <visible>!String.IsEmpty(ListItem.Thumb)</visible>
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <aspectratio>scale</aspectratio>
                    <texture diffuse="script.plex/white-square-rounded.png" background="true">$INFO[ListItem.Thumb]</texture>
                </control>
                <control type="image">
                    <visible>String.IsEmpty(ListItem.Thumb) + !String.IsEmpty(ListItem.Label2)</visible>
                    <posx>70</posx>
                    <posy>{{ vscale(46) }}</posy>
                    <width>100</width>
                    <height>{{ vscale(100) }}</height>
                    <texture colordiffuse="FF8A93A0">script.plex/jellyfin/avatar.png</texture>
                </control>
                <control type="label">
                    <visible>String.IsEmpty(ListItem.Label2)</visible>
                    <posx>20</posx>
                    <posy>0</posy>
                    <width>200</width>
                    <height>{{ vscale(200) }}</height>
                    <font>font45_title</font>
                    <align>center</align>
                    <aligny>center</aligny>
                    <textcolor>FFFFFFFF</textcolor>
                    <label>+</label>
                </control>
                <control type="label">
                    <posx>0</posx>
                    <posy>{{ vscale(212) }}</posy>
                    <width>240</width>
                    <height>{{ vscale(40) }}</height>
                    <font>font10</font>
                    <align>center</align>
                    <textcolor>FFFFFFFF</textcolor>
                    <label>$INFO[ListItem.Label]</label>
                </control>
            </control>
            <control type="image">
                <visible>Control.HasFocus(101)</visible>
                <posx>14</posx>
                <posy>{{ vscale(-6) }}</posy>
                <width>212</width>
                <height>{{ vscale(212) }}</height>
                <texture colordiffuse="FF2563EB" border="10">script.plex/white-outline-rounded.png</texture>
            </control>
        </focusedlayout>
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
