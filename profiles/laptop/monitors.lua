-- Original laptop: eDP-1 + mirrored HDMI, 150% scaling.
hl.monitor({ output = "eDP-1", mode = "1920x1080@59.93", position = "0x0", scale = 1.5 })
hl.monitor({ output = "HDMI-A-1", mode = "1920x1080@60", position = "auto", scale = 1.5, mirror = "eDP-1" })
