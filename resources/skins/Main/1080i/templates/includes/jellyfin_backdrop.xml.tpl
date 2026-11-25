<!-- Jellyfin's sign-in backdrop (its Modern login page): a slate gradient under a top bar with Jellyfin's logo -->
<control type="image">
    <posx>0</posx>
    <posy>0</posy>
    <width>1920</width>
    <height>1080</height>
    <texture>script.plex/jellyfin/background.png</texture>
</control>
<control type="image">
    <posx>0</posx>
    <posy>0</posy>
    <width>1920</width>
    <height>{{ vscale(72) }}</height>
    <texture colordiffuse="FF1D2735">script.plex/white-square.png</texture>
</control>
<control type="image">
    <posx>0</posx>
    <posy>{{ vscale(72) }}</posy>
    <width>1920</width>
    <height>1</height>
    <texture colordiffuse="FF47505C">script.plex/white-square-1px.png</texture>
</control>
<control type="image">
    <posx>60</posx>
    <posy>{{ vscale(14) }}</posy>
    <width>44</width>
    <height>{{ vscale(44) }}</height>
    <aspectratio>keep</aspectratio>
    <texture>script.plex/jellyfin/logo.png</texture>
</control>
