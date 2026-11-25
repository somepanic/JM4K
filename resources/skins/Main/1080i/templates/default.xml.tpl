{% extends "base.xml.tpl" %}{# this extends base and adds background and default header blocks #}
{# The background's flat base: Kodi clears to it, in place of a full-screen image (includes/default_background.xml.tpl). #}
{% block backgroundcolor %}<backgroundcolor>0xff111111</backgroundcolor>{% endblock %}
{% block controls %}
    {% block background %}
        {% include "includes/default_background.xml.tpl" %}
    {% endblock %}
    {# Everything but the background, hidden while post-play hands over to an item opened from its
       rows (nav_hidden, videoplayer.py's openItem()): the screen that started playback shows for a
       moment between post-play closing and the item's screen replacing it. Cleared as the next
       window first initialises (kodigui.BaseWindow._onInit()). A global property, not a window
       one, since Kodi hands a reused window id's properties on to the next window. #}
    <control type="group">
    <visible>String.IsEmpty(Window(10000).Property(script.plex.nav_hidden))</visible>
    <!-- block content -->
    {% block content %}{% endblock %}

    {% block header %}
    <control type="group" id="200">
        {% block header_anim %}<animation effect="slide" end="0,{{ vscale(-300) }}" time="200" tween="quadratic" easing="out" condition="!String.IsEmpty(Window.Property(on.extras)) + !ControlGroup(200).HasFocus(0)">Conditional</animation>{% endblock %}
        <defaultcontrol always="true">201</defaultcontrol>
        <posx>0</posx>
        <posy>0</posy>
        <width>1920</width>
        <height>{{ vscale(125) }}</height>
        {% block header_bgfade %}
        <control type="image">
            <animation effect="fade" start="0" end="100" time="200" tween="quadratic" easing="out" reversible="true">VisibleChange</animation>
            <visible>ControlGroup(200).HasFocus(0) + !String.IsEmpty(Window.Property(on.extras))</visible>
            <posx>0</posx>
            <posy>0</posy>
            <width>1920</width>
            <height>{{ vscale(125) }}</height>
            <texture>script.plex/white-square.png</texture>
            <colordiffuse>C0000000</colordiffuse>
        </control>
        {% endblock %}
        {% block header_sidebar %}{% endblock %}
        {% block header_middle_add %}{% endblock %}
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
    </control>

    {% endblock header %}
    </control>
    {% include "includes/dialog_gap_dim.xml.tpl" %}
{% endblock controls %}