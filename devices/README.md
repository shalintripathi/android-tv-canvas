# Device lists

A device list is a plain text file of apps that come preinstalled on a TV and that you may not want: promos, shop-demo modes, trial apps, "recommended apps" installers. `tools/canvas debloat` reads a list, tells you what each app is, and switches off only the groups you say yes to.

Nothing is ever uninstalled. Each app is switched off for the main user (`pm disable-user --user 0`) and can be switched back on at any time.

| List | For | Status |
|---|---|---|
| [`google-tv-common.txt`](google-tv-common.txt) | Any Google TV: Google's own extras (Google TV app, Play Games, feedback) | Packages are the ones on the tested TV; not tried on other brands |
| [`sony-bravia-bf1.txt`](sony-bravia-bf1.txt) | Sony BRAVIA BF1, Android 12 Google TV | Tested on this TV |

You do not need a list for your exact TV. A list only names packages; one that names a package your TV does not have just reports it and carries on.

## Using a list

```bash
tools/canvas debloat devices/google-tv-common.txt
```

For each group, `debloat` prints the packages and what each one is, then asks:

```
Google extras
  com.google.android.videos      Google TV app / Play Movies & TV
  com.google.android.play.games  Google Play Games
  com.google.android.feedback    sends feedback and bug reports to Google
Disable this group? [y/N]
```

- The default is **No**. Press Enter, or type anything other than `y` or `yes`, and the group is left alone.
- Ctrl-C or Ctrl-D at any question cancels the whole run. Nothing has been changed on the TV by then, because the TV is only touched after the last question.
- `--group "Google extras"` applies that one group without asking (the name is not case-sensitive).
- `--yes` applies every group without asking.
- If the TV rejects a package (for example it is not installed), `debloat` says which one and why, goes on with the rest, and exits with an error at the end so you notice.

## Undo

```bash
tools/canvas undebloat devices/google-tv-common.txt
```

It asks the same way and takes the same `--group` and `--yes` options, but switches the apps back on. You can also do it on the TV itself: in Settings, open the list of all apps including system apps (the menu names differ between TVs), pick the app and choose **Enable**.

`tools/canvas restore` is a different command: it undoes `tools/canvas install` (the home screen and screensaver), not `debloat`.

## File format

```
# Anything after a hash on its own line is a comment.

## Sony promos
com.sony.dtv.promos            # what this package is, in plain words
com.sony.dtv.sonytab           # the "Sony" content tab of the stock launcher

## Google extras
com.google.android.videos      # Google TV app / Play Movies & TV
```

- `## Name` starts a group. The group is what you are asked about, so put apps that belong together under one name, and say in the name what it is. The same name twice is one group.
- Every other line is `package  # description`. The description is what `debloat` shows next to the package, so write it for someone who has never seen the package name.
- Packages above the first `## ` line go in a group called "Other".
- Blank lines and lines that start with a single `#` are ignored.

## Finding packages on your TV

Connect once with `tools/canvas find`, then:

```bash
tools/canvas sh 'pm list packages'        # everything installed
tools/canvas sh 'pm list packages -s'     # system apps only
tools/canvas sh 'pm list packages -d'     # what is switched off right now
tools/canvas probe                        # model, Android version and build, for the header of your list
```

Search for a package name before you list it. If you cannot tell what an app does, leave it out.

## What never goes in a list

Switching these off can leave a TV unable to search, type, tune, install apps or show web content, and some are what the TV falls back on when something else goes wrong:

- voice search and the assistant (for example `com.google.android.katniss`);
- keyboards and other input methods;
- TV tuners, TV inputs and Live TV;
- Settings (`com.android.tv.settings`, `com.android.settings`);
- the Play Store and Google Play services (`com.android.vending`, `com.google.android.gms`);
- Android System WebView (`com.google.android.webview`);
- the TV's own home app. `tools/canvas install` switches that off itself, with a backup and a check that Canvas really took over, and `tools/canvas restore` brings it back.

## Contributing a list

Lists for other TVs and streamers are welcome. Name the file after the device (`tcl-c635.txt`, `xiaomi-mi-box-s.txt`), put the output of `tools/canvas probe` (model, Android version, build) in a comment at the top, give every package a description, and only list apps you have switched off on that device and seen it keep working. Open a pull request, or an issue if you would like help.
