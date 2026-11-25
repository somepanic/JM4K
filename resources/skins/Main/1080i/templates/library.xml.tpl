{% extends "default.xml.tpl" %}
{% block headers %}<defaultcontrol>100</defaultcontrol>{% endblock %}

{# No top-right hero-art box on anything in this chain - the poster/square grids and both list
   views (via library_posters.xml.tpl) plus Genres. Only Recommended's hub-focused item wants it,
   and script-plex-recommended.xml.tpl extends default.xml.tpl directly, so it is unaffected.
   Replaces the runtime no_hero_art property for these screens; see default_background.xml.tpl's
   own comment on the flag for the view-swap bug that made the property approach unreliable. #}
{% block background %}{% include "includes/default_background.xml.tpl" with suppress_hero_art=True %}{% endblock %}

{# header_middle_add is default.xml.tpl's hook, but the header block below fully replaces
   default.xml.tpl's header body (see {% block header %} immediately following) and never
   references header_middle_add - so overriding that block name here would be dead code,
   never rendered. Inlined directly into the real body instead (right after the audio-widget
   group, before filteropts_grouplist), same as script-plex-recommended.xml.tpl does. #}

{% block header %}
<control type="group" id="200">
    {% block header_animation %}<animation effect="slide" end="0,{{ vscale(-125) }}" time="200" tween="quadratic" easing="out" condition="[[Integer.IsGreater(Container(101).CurrentItem,6) + String.IsEmpty(Window.Property(content.filling))] | Integer.IsGreater(Window.Property(restore.item),6)] + !ControlGroup(200).HasFocus(0)">Conditional</animation>{% endblock %}
    {% block header_defaultcontrol %}{% endblock %}
    <posx>0</posx>
    <posy>0</posy>
    <width>1920</width>
    <height>{{ vscale(125) }}</height>
    <visible>!String.IsEmpty(Window.Property(initialized))</visible>
    {% block header_bg %}
    <control type="image">
        <animation effect="fade" start="0" end="100" time="200" tween="quadratic" easing="out" reversible="true">VisibleChange</animation>
        <visible>ControlGroup(200).HasFocus(0) + Integer.IsGreater(Container(101).CurrentItem,6)</visible>
        <posx>0</posx>
        <posy>0</posy>
        <width>1920</width>
        <height>{{ vscale(125) }}</height>
        <texture>script.plex/white-square.png</texture>
        <colordiffuse>C0000000</colordiffuse>
    </control>
    {% endblock %}
    <control type="label">
        <right>60</right>
        <posy>{{ vscale(30) }}</posy>
        <width>200</width>
        <height>{{ vscale(65) }}</height>
        <font>font12</font>
        <align>right</align>
        <aligny>center</aligny>
        <textcolor>FFFFFFFF</textcolor>
        <label>$INFO[System.Time]</label>
    </control>
    {% with tab_ondown = 101 %}{% include "includes/section_tabs.xml.tpl" %}{% endwith %}
    {% block filteropts_grouplist %}
    <control type="grouplist"{% block filteropts_grouplist_attrs %} id="600"{% endblock %}>
        <visible>String.IsEmpty(Window.Property(hide.filteroptions))</visible>
        <visible>!Integer.IsGreater(Container(101).CurrentItem,{% block hide_filter_after_item %}6{% endblock %}) + !Integer.IsGreater(Window.Property(restore.item),{% block hide_filter_after_item %}6{% endblock %}) + String.IsEmpty(Window.Property(no.content)) + !String.IsEmpty(Window.Property(initialized))</visible>
        <animation effect="slide" time="200" end="0,{{ vscale(-115) }}" tween="quadratic" easing="out" condition="[Integer.IsGreater(Container(101).CurrentItem,{% block hide_filter_after_item %}6{% endblock %}) + String.IsEmpty(Window.Property(content.filling))] | Integer.IsGreater(Window.Property(restore.item),{% block hide_filter_after_item %}6{% endblock %})">Conditional</animation>
        {% block filteropts_animation %}
            <animation effect="fade" start="0" end="100" time="200" reversible="true">VisibleChange</animation>
        {% endblock %}
        <right>170</right>
        <posy>{{ vscale(125) }}</posy>
        <width>1000</width>
        <height>{{ vscale(65) }}</height>
        <align>right</align>
        <itemgap>30</itemgap>
        <orientation>horizontal</orientation>
        {% block header_filteropts_onleft_nocontent %}<onleft>9000</onleft>{% endblock %}
        <onright>300</onright>
        <ondown>101</ondown>
        {% block header_filteropts_onup %}<onup condition="Control.IsVisible(320)">320</onup><onup condition="Player.HasAudio + String.IsEmpty(Window(10000).Property(script.plex.theme_playing))">204</onup>{% endblock %}
        <control type="button" id="311">
            <enable>false</enable>
            <width max="300">auto</width>
            <height>{{ vscale(65) }}</height>
            <font>font12</font>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FFFFFFFF</focusedcolor>
            <disabledcolor>FFFFFFFF</disabledcolor>
            <align>center</align>
            <aligny>center</aligny>
            <texturefocus>-</texturefocus>
            <texturenofocus>-</texturenofocus>
            <textoffsetx>0</textoffsetx>
            <textoffsety>0</textoffsety>
            <label>[CAPITALIZE]$INFO[Window.Property(filter2.display)][/CAPITALIZE]</label>
        </control>
        <control type="button" id="211">
            <width max="500">auto</width>
            <height>{{ vscale(65) }}</height>
            <font>font12</font>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FF000000</focusedcolor>
            <align>center</align>
            <aligny>center</aligny>
            <texturefocus colordiffuse="FFE5A00D" border="10">script.plex/white-square-rounded.png</texturefocus>
            <texturenofocus>-</texturenofocus>
            <textoffsetx>20</textoffsetx>
            <textoffsety>0</textoffsety>
            <label>[CAPITALIZE]$INFO[Window.Property(filter1.display)][/CAPITALIZE]</label>
        </control>
        <control type="button" id="310">
            <visible>!String.IsEqual(Window.Property(media),artist)</visible>
            <enable>false</enable>
            <width max="300">auto</width>
            <height>{{ vscale(65) }}</height>
            <font>font12</font>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FFFFFFFF</focusedcolor>
            <disabledcolor>FFFFFFFF</disabledcolor>
            <align>center</align>
            <aligny>center</aligny>
            <texturenofocus>-</texturenofocus>
            <textoffsetx>20</textoffsetx>
            <textoffsety>0</textoffsety>
            <label>[CAPITALIZE]$INFO[Window.Property(media.type)][/CAPITALIZE]</label>
        </control>
        <control type="button" id="312">
            <visible>String.IsEqual(Window.Property(media),artist)</visible>
            <width max="300">auto</width>
            <height>{{ vscale(65) }}</height>
            <font>font12</font>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FF000000</focusedcolor>
            <disabledcolor>FFFFFFFF</disabledcolor>
            <align>center</align>
            <aligny>center</aligny>
            <texturefocus colordiffuse="FFE5A00D" border="10">script.plex/white-square-rounded.png</texturefocus>
            <texturenofocus>-</texturenofocus>
            <textoffsetx>20</textoffsetx>
            <textoffsety>0</textoffsety>
            <label>[CAPITALIZE]$INFO[Window.Property(media.type)][/CAPITALIZE]</label>
        </control>
        <control type="button" id="212">
            <!-- Ascending/descending indicator for the sort button below - see sortButtonClicked()/
                 updateSortIcon() (library.py). type=button + enable=false, not type=image: a
                 plain image here isn't a focusable-eligible control type, which breaks the
                 grouplist's internal navigation. Direct grouplist child at the row's own full
                 height (not a shorter box + <posy>, and not wrapped in a group): a shorter box
                 with an explicit posy offset - even nested one level inside a group - measurably
                 broke this row's right-navigation out to the play button when this sat after 210
                 instead of before it, for reasons that didn't trace back to any onright value.
                 Matching every sibling's plain full-height footprint is what's proven not to
                 disturb it, so the vertical offset is baked into the sort-asc/desc.png canvas's
                 own transparent padding instead of a posy tag. Two mutually-exclusive
                 static-texture buttons, not one dynamic $INFO path - $INFO[Window.Property(...)]
                 isn't evaluated inside <texturenofocus> the way it is inside an image control's
                 <texture>, so that only rendered an empty box. Same swap-on-a-property pattern
                 310/312 already use above for the media-type button's artist variant. -->
            <visible>!String.IsEqual(Window.Property(sort.icon),desc)</visible>
            <enable>false</enable>
            <width>{{ vscale(30) }}</width>
            <height>{{ vscale(65) }}</height>
            <texturefocus>-</texturefocus>
            <texturenofocus>script.plex/indicators/sort-asc.png</texturenofocus>
        </control>
        <control type="button" id="213">
            <visible>String.IsEqual(Window.Property(sort.icon),desc)</visible>
            <enable>false</enable>
            <width>{{ vscale(30) }}</width>
            <height>{{ vscale(65) }}</height>
            <texturefocus>-</texturefocus>
            <texturenofocus>script.plex/indicators/sort-desc.png</texturenofocus>
        </control>
        <control type="button" id="210">
            <!-- Explicit, not relying on the grouplist's own onright: nothing else follows 210
                 in this (unused) template. Targets 301 (the play button) directly, not
                 container 300. -->
            <onright>301</onright>
            <width max="300">auto</width>
            <height>{{ vscale(65) }}</height>
            <font>font12</font>
            <textcolor>FFFFFFFF</textcolor>
            <focusedcolor>FF000000</focusedcolor>
            <align>center</align>
            <aligny>center</aligny>
            <texturefocus colordiffuse="FFE5A00D" border="10">script.plex/white-square-rounded.png</texturefocus>
            <texturenofocus>-</texturenofocus>
            <textoffsetx>20</textoffsetx>
            <textoffsety>0</textoffsety>
            <label>[CAPITALIZE]$INFO[Window.Property(sort.display)][/CAPITALIZE]</label>
        </control>
    </control>
    {% endblock filteropts_grouplist %}
</control>

{# The sidebar rail lives outside group 200 deliberately, mirroring script-plex-home.xml.tpl -
   group 200 slides off-screen on scroll (header_animation above), and nothing in the sidebar
   (including the user/server buttons) should move when that happens. #}
{% block header_sidebar %}{% include "includes/sidebar.xml.tpl" %}{% endblock %}

{% block no_content %}
<control type="group">
    <visible>!String.IsEmpty(Window.Property(no.content)) + String.IsEmpty(Window.Property(server.unavailable))</visible>
    <posx>0</posx>
    <posy>{{ vscale(465) }}</posy>
    {# an empty library, or an empty Playlists section (no.content.playlists, fillPlaylists()) #}
    <control type="label">
        <scroll>false</scroll>
        <posx>60</posx>
        <posy>0</posy>
        <width>1800</width>
        <height>{{ vscale(35) }}</height>
        <font>font13</font>
        <align>center</align>
        <textcolor>FFFFFFFF</textcolor>
        <visible>String.IsEmpty(Window.Property(no.content.playlists))</visible>
        <label>[B]$ADDON[script.plexmod 35132][/B]</label>
    </control>
    <control type="label">
        <scroll>false</scroll>
        <posx>60</posx>
        <posy>{{ vscale(60) }}</posy>
        <width>1800</width>
        <height>{{ vscale(35) }}</height>
        <font>font13</font>
        <align>center</align>
        <textcolor>FFCCCCCC</textcolor>
        <visible>String.IsEmpty(Window.Property(no.content.playlists))</visible>
        <label>$ADDON[script.plexmod 35133]</label>
    </control>
    <control type="label">
        <scroll>false</scroll>
        <posx>60</posx>
        <posy>0</posy>
        <width>1800</width>
        <height>{{ vscale(35) }}</height>
        <font>font13</font>
        <align>center</align>
        <textcolor>FFFFFFFF</textcolor>
        <visible>!String.IsEmpty(Window.Property(no.content.playlists))</visible>
        <label>[B]$ADDON[script.plexmod 35134][/B]</label>
    </control>
    <control type="label">
        <scroll>false</scroll>
        <posx>60</posx>
        <posy>{{ vscale(60) }}</posy>
        <width>1800</width>
        <height>{{ vscale(35) }}</height>
        <font>font13</font>
        <align>center</align>
        <textcolor>FFCCCCCC</textcolor>
        <visible>!String.IsEmpty(Window.Property(no.content.playlists))</visible>
        <label>$ADDON[script.plexmod 35135]</label>
    </control>
</control>

<control type="group">
    <visible>!String.IsEmpty(Window.Property(no.content.filtered)) + String.IsEmpty(Window.Property(server.unavailable))</visible>
    <posx>0</posx>
    <posy>{{ vscale(465) }}</posy>
    <control type="label">
        <scroll>false</scroll>
        <posx>60</posx>
        <posy>0</posy>
        <width>1800</width>
        <height>{{ vscale(35) }}</height>
        <font>font13</font>
        <align>center</align>
        <textcolor>FFFFFFFF</textcolor>
        <label>[B]$ADDON[script.plexmod 32454][/B]</label>
    </control>
    <control type="label">
        <scroll>false</scroll>
        <posx>60</posx>
        <posy>{{ vscale(60) }}</posy>
        <width>1800</width>
        <height>{{ vscale(35) }}</height>
        <font>font13</font>
        <align>center</align>
        <textcolor>FFCCCCCC</textcolor>
        <label>$ADDON[script.plexmod 32455]</label>
    </control>
</control>

{% endblock %}

{% include "includes/server_unavailable.xml.tpl" %}

{% endblock header %}