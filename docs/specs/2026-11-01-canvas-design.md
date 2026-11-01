# Canvas for Android TV: design

Date: 2026-11-01. Status: approved in conversation, awaiting written review.

## 1. What we are building

Canvas is one Android app for Android TV and Google TV with two modes that share one engine:

- **Home mode**: a fast, ad-free home screen plus a photo-frame screensaver. Photos come from the
  user's phone over Wi-Fi (on Android this includes Google Photos through the phone's photo picker).
- **Business mode** (later milestone): while the TV is idle during business hours it becomes a
  digital-signage panel showing the business's own offers.

What the TV shows when nobody is watching is the product. The home screen and the phone page are
how people get content onto it.

It starts from [mihirgupta0900/tv-home](https://github.com/mihirgupta0900/tv-home) (MIT): a tiny
Apple TV-style launcher plus ADB debloat scripts. Canvas is a rewrite, not a fork: new build,
structure and features, crediting tv-home and reusing its design and its CC BY-SA wallpaper set.

## 2. Findings that constrain the design

Researched in November 2026; sources are in the conversation's research briefs.

| Finding | Consequence |
|---|---|
| Since 31 Mar 2025 the Google Photos Library API only reads app-created media. The Picker API needs a per-session pick, and its scope is not allowed in the OAuth device flow for TVs. Live album sync (Ambient API) is partner-only. | No direct Google Photos sync. Photos arrive through the phone page; on Android the browser's file input opens the system photo picker, which includes Google Photos. A Google Photos sign-in on the TV (Play services `AuthorizationClient` and the Picker API) is a time-boxed experiment in milestone 2. |
| Google TV hides third-party screensavers, but `settings put secure screensaver_components` works on Android 12 and 14. `WRITE_SECURE_SETTINGS` can be granted with `pm grant`. | Canvas ships a `DreamService`. The install script grants the permission; the app sets and repairs its own screensaver settings. |
| Video playback (`FLAG_KEEP_SCREEN_ON`) blocks the screensaver. | The screensaver never interrupts viewing. |
| Apps cannot reliably wake a TV from standby, and inattentive sleep turns the screen off despite wake locks. | Business mode (later) keeps the TV awake during hours through `attentive_timeout`/`sleep_timeout`. Waking the TV at opening time is left to the TV's own power-on timer. |
| On Google TV the only reliable way to replace Home is to disable `com.google.android.apps.tv.launcherx` and `com.google.android.tungsten.setupwraith`. Google updates have broken other override tricks. | Home mode does that, reversibly. The screensaver and the app must work even if Home is lost. |
| macOS Local Network privacy blocks non-Apple binaries (Homebrew Python, `adb`) from reaching LAN hosts; Apple's `nc` is allowed. | The tooling relays ADB through `nc` on localhost when a direct connection fails. |

The test TV: Sony BRAVIA (BF1, MediaTek mt5896), Android 12 / API 31, Google TV, 2.4 GB
RAM (~970 MB free), UI 1920×1080 at density 320 (960×540 dp). Stock home
`com.google.android.apps.tv.launcherx` (88 MB PSS idle). Stock screensaver
`com.google.android.apps.tv.dreamx/.service.Backdrop`, `screen_off_timeout` 600000 ms,
`sleep_timeout` 900000 ms. HOME candidates: launcherx, setupwraith `RecoveryActivity`,
`com.android.tv.settings/.system.FallbackHome`. Live TV app: `com.mediatek.tv.oneworld.tvcenter`.

## 3. Scope

**Milestone 1 (this spec's build):**
1. Repo, Gradle project, CI that tests every push and attaches a signed APK to tagged releases.
2. Home screen (section 5).
3. Screensaver with the built-in photos and phone-uploaded photos (section 6).
4. Phone page with pairing, upload, library management and settings (section 7).
5. Settings helper that applies and repairs screensaver and timeout settings (section 8).
6. Install, restore and debloat tooling (section 9).
7. A README written for search and AI answer engines (section 11).

**Milestone 2:** USB photos, art mode (public-domain works from the Met and the Art Institute of
Chicago open-access APIs), night mode (dim, warm, clock-only late at night), weather (MET Norway,
CC BY 4.0, usable commercially), "on this day" curation, Immich and Nextcloud sources, the Google
Photos sign-in experiment, and a GitHub Pages site with structured data.

**Milestone 3, Business mode:** playlist of images, MP4 and PDF pages with per-item duration,
dayparting and expiry; fill-in templates (offer card, price list or menu board, notice); overlays
(logo, ticker, clock, QR for UPI, Google review, WhatsApp and Instagram); business hours (signage
when idle in hours, sleep after closing); PIN-protected settings; instant message override. Media3
ExoPlayer for video.

**Milestone 4:** clinic token mode (Next/Recall from a phone, chime and Hindi/English
announcement, doctor status), Google Drive folder and Sheets CSV sources, proof-of-play counts,
cloud management for many screens.

**Out of scope for all milestones:** Samsung Tizen, LG webOS, Roku and VIDAA TVs (no Android
apps); device-owner kiosk provisioning; third-party ad networks until there is a cloud backend.

## 4. Compatibility and stack

- **Android 7.0+ (minSdk 24), targetSdk 35.** Newer APIs are used behind version checks with
  fallbacks: `ImageDecoder` (API 28) falls back to `BitmapFactory`; `getChangedPackages` (API 26)
  falls back to package broadcasts plus a rescan on resume.
- **No native code**, so the APK runs on 32-bit and 64-bit userspaces. No libraries that bundle
  `.so` files.
- **Kotlin, Gradle (wrapper), framework Views.** No Jetpack Compose, no AndroidX UI. Milestone 1
  dependencies: Kotlin stdlib and a QR encoder (ZXing core, pure Java). Media3 arrives with
  milestone 3.
- **Package** `io.github.shalintripathi.canvas`, app label **Canvas**.
- **Fire TV**: the app installs and runs as a normal app. Amazon blocks Home replacement and
  third-party screensavers there, so Fire TV only becomes useful with Business mode.
- **Performance budget, measured on the Sony**: Home visible within 600 ms of pressing Home
  (`am start -W` warm start); under 80 MB PSS while Home is in front; at most three decoded
  1080p bitmaps alive at once.

## 5. Home screen (`HomeActivity`)

Behaviour kept from tv-home:
- Top left: greeting, clock, date. Top right: pills for Inputs, Wi-Fi, Settings, plus a new
  **Phone** pill.
- Bottom: a dock of pinned apps on a frosted band. The focused dock app's name shows above it over
  a glow coloured from its banner.
- Down from the dock scrolls to an alphabetical grid of all other apps (5 columns).
- Wallpaper layer crossfades every 10 minutes under a readability scrim.
- Deterministic D-pad focus, remembers the last focused tile, rescans packages on resume, Menu key
  opens Inputs, Home while already home returns focus to the first dock app.

Changes:
- **Dock and hidden apps are user data**, not build-time config. Defaults: pin the first five
  installed of YouTube, Netflix, Prime Video, Hotstar, Apple TV; hide system helpers. Long-press
  OK on a tile opens a small menu: Move left, Move right, Add to dock / Remove from dock, Hide.
  Hidden apps can be restored from the phone page.
- **Wallpaper source**: "My photos" (the photo library, when it has photos) or "Built-in".
- **Inputs menu** discovers the Live TV app instead of hard-coding it: the first installed of a
  known list (`com.mediatek.tv.oneworld.tvcenter`, `com.mediatek.wwtv.tvcenter`, `com.tcl.tv`,
  `com.sony.dtv.tvx`, `com.google.android.tv`), listed after HDMI/AV passthrough inputs.
- **Phone pill** opens the pairing screen (section 7).
- The UI is split into small classes: `AppRepository` (query, labels, banners, colour sampling),
  `DockView`, `AppGridView`, `HeaderView`, `InputsMenu`, `WallpaperLayer`, `TileMenu`.

## 6. Screensaver (`FrameDreamService`)

- A `DreamService` that hosts `SlideshowView`, the shared rendering engine (Business mode adds
  slide types to it later). `setInteractive(true)`: Left/Right show the previous/next slide; any
  other key calls `finish()` so the TV wakes to whatever was on screen before.
- **Slides**: one landscape photo full-bleed (centre-crop), or two portrait photos side by side
  (each 960×1080, centre-crop). A portrait without a portrait neighbour is shown centred over a
  blurred, darkened copy of itself.
- **Motion**: each slide holds for N seconds (default 30, range 10–300), crossfades over 1.5 s,
  and runs a slow Ken Burns move (scale 1.0 → 1.08 plus a small pan, random direction).
- **Order**: shuffle bag; no repeats until every photo has been shown; favourites go into the bag
  twice. Pairing happens at bag-draw time: drawing a portrait pulls the next portrait in the bag
  forward.
- **Overlays**: clock and date (one corner) and a caption (the other corner), for example
  "March 2023 · 3 years ago", from the photo's capture date, or nothing if there is no date. The
  overlay block shifts by up to 24 dp every 5 minutes to avoid burn-in.
- **Fallback**: an empty library uses the built-in photos.
- **Memory**: decode with `inSampleSize` or `ImageDecoder.setTargetSize` to the screen size, keep
  the current and next slides only, decode off the main thread, release on `onDetachedFromWindow`.

## 7. Phone page and pairing (`CompanionServer`)

- A small HTTP/1.1 server on a background thread listening on port **8765** on all interfaces.
  It runs while the app process is alive (Home or screensaver in front) and stops when the
  process dies.
- **Pairing**: the Phone screen on the TV shows a QR code for
  `http://<tv-ip>:8765/pair?t=<128-bit token>` and, for manual entry, `http://<tv-ip>:8765` plus a
  6-digit PIN. Token and PIN are created when the screen opens and expire after 10 minutes. A
  successful pair sets an `HttpOnly` cookie holding a random 256-bit session id. The TV stores
  only its SHA-256 hash, plus a label from the User-Agent and the pairing date. The PIN allows 5
  wrong attempts per code, after which the code is void.
- Every `/api/*` request without a valid session gets `401`. Plain HTTP on the LAN is accepted
  for home use and documented as a limitation.
- **The page**: one static HTML/CSS/JS file in `assets/web/`. No framework, no CDN, works offline,
  works in Chrome on Android and Safari on iOS. Sections: Photos, Screensaver, Home screen, Phones.
- **API** (JSON unless noted):

| Method and path | Purpose |
|---|---|
| `GET /` | App page if paired, otherwise the PIN page |
| `GET /pair?t=` | Pair with the QR token, then redirect to `/` |
| `POST /api/pair` `{pin}` | Pair with the PIN |
| `GET /api/photos` | List `{id, takenAt, width, height, favorite, source}` |
| `PUT /api/photos?name=&takenAt=` (raw image body) | Upload one photo; progress through XHR upload events |
| `DELETE /api/photos/{id}` | Delete a photo |
| `PUT /api/photos/{id}/favorite` `{favorite}` | Toggle favourite |
| `GET /api/photos/{id}/thumb` (JPEG) | 320 px thumbnail |
| `GET /api/settings`, `PUT /api/settings` | Screensaver and home settings |
| `GET /api/apps` | Installed apps with dock and hidden state |
| `GET /api/apps/{pkg}/icon` (PNG) | App banner or icon |
| `PUT /api/home` `{dock:[pkg], hidden:[pkg]}` | Save dock order and hidden apps |
| `GET /api/phones`, `DELETE /api/phones/{id}` | List and revoke paired phones |

- **Upload handling**: body limit 40 MB. The TV reads the capture date with `ExifInterface`
  (falling back to the `takenAt` query parameter, which the page fills from `File.lastModified`),
  applies the EXIF orientation, downsamples so the long side is 1920 px, re-encodes JPEG at
  quality 88 into `files/photos/<id>.jpg`, writes a 320 px thumbnail to `files/thumbs/<id>.jpg`,
  and appends to `files/library.json`. Undecodable files get `415` with a readable message (for
  example HEIC on TVs below Android 9).

## 8. Settings helper (`SystemSettings`)

- Reads and writes `screensaver_components`, `screensaver_enabled`,
  `screensaver_activate_on_sleep`, `sleep_timeout` (Secure) and `screen_off_timeout` (System).
- Needs `WRITE_SECURE_SETTINGS` (granted by `pm grant`) and `WRITE_SETTINGS` (granted by
  `appops set ... WRITE_SETTINGS allow`). Without them the phone page shows the settings read-only
  with a "run tools/install again" note.
- On boot and on every Home resume, if "Use Canvas as screensaver" is on and
  `screensaver_components` points elsewhere, it is set back to Canvas. This covers TV updates and
  Google's Ambient settings resetting it.
- Phone-page settings map to: "Start screensaver after" → `screen_off_timeout` (5, 10, 15, 30 min);
  "Turn TV off after" → `sleep_timeout` (15 min, 30 min, 1 h, 4 h, never = -1).

## 9. Tooling (`tools/`)

- `tools/canvas` (Python 3, `adb-shell` package, run from its own venv), with subcommands:
  `find` (scan the /24 for ports 5555 and 8008, list mDNS Android TVs, save the host), `probe`,
  `shot <png>`, `install [apk]`, `restore`, `debloat <list>`, `undebloat <list>`.
- **Connection**: try the host directly; on `EHOSTUNREACH` on macOS, start an `nc` relay on
  `127.0.0.1` and connect through it.
- **`install`**: back up the previous home activity, screensaver and timeout settings, and the
  disabled-package list to `tools/backups/<serial>-<timestamp>.json`. Then install the APK; grant
  `WRITE_SECURE_SETTINGS` and `WRITE_SETTINGS`; `cmd package set-home-activity`; disable launcherx
  and setupwraith (one `pm` call per shell call); set the Canvas screensaver; force-stop
  `com.android.tv.settings`; press Home; take a screenshot.
- **`restore`**: re-enable the packages Canvas disabled, restore the backed-up settings, reset the
  home activity, and optionally uninstall Canvas.
- **Debloat lists** live in `devices/<model>.txt` (format as in tv-home: one package per line,
  with comments). Packages are disabled for user 0, never uninstalled. The Sony list is proposed
  to the owner and approved app by app before it is applied.

## 10. Testing

- **JVM unit tests** (JUnit 4, no Robolectric) for logic kept free of Android types: shuffle bag
  and portrait pairing, caption text from dates, HTTP request parsing and limits, pairing tokens,
  PIN attempts and session hashing, the library index round trip, the dock/hidden defaults.
- **On the Sony, scripted through ADB**: screenshots of Home, grid, Inputs, tile menu, Phone
  screen and screensaver; Home pressed from inside an app returns to Canvas; screensaver starts
  after the timeout and not while video plays; Left/Right during the screensaver; memory
  (`dumpsys meminfo`) and warm start time (`am start -W`). One manual step: the owner scans the QR
  code once for an end-to-end upload from a real phone.
- **Older Android**: emulator images for API 24 and 28. On Apple Silicon these are arm64 phone
  images (the TV images for those APIs are x86 only), so they cover API compatibility, not TV
  behaviour. This depends on free disk space.
- **CI** (GitHub Actions): `./gradlew test assembleDebug lint` on every push and pull request.
  Tags `v*` build a release APK signed with a key held in repository secrets and attach it to the
  GitHub Release. The release keystore lives outside the repo, and the owner keeps a backup.

## 11. Repo and discovery

- `github.com/shalintripathi/android-tv-canvas`, public, MIT, with tv-home credited in the README
  and LICENSE notice. Wallpapers keep their CC BY-SA credits in `WALLPAPER-CREDITS.md`.
- **Description**: "Fast, ad-free home screen for Android TV & Google TV with a photo-frame
  screensaver (add photos from your phone, including Google Photos) and free digital signage for
  shops and clinics. No account, no cloud."
- **Topics (20)**: android-tv, google-tv, android-tv-launcher, launcher, home-screen, screensaver,
  photo-frame, digital-photo-frame, digital-signage, signage, google-photos, adb, debloat,
  sony-bravia, tcl, mi-box, nvidia-shield, chromecast-with-google-tv, kotlin, smart-tv.
- **README written for search and AI answers**: a one-sentence answer first; before/after
  screenshots and a short GIF; two-step install from Releases; an FAQ phrased as real queries ("How
  do I remove ads from the Google TV home screen?", "Can Android TV show Google Photos as a
  screensaver?", "Free digital signage on a TV I already own?"); a comparison table (Projectivy,
  FLauncher, Google Ambient mode, Yodeck); a supported-TVs table; restore instructions.

## 12. Risks

| Risk | Mitigation |
|---|---|
| A Google TV update re-enables launcherx or blocks Home replacement | The screensaver works without Home; `tools/canvas install` re-applies; documented. |
| The screensaver does not start below about 6 minutes on Android 12+ | The phone page offers 5 minutes and up; verified on the Sony. |
| Phone uploads in HEIC fail on old TVs | `415` with a clear message; iOS Safari already converts to JPEG for `image/*` uploads. |
| Router client isolation blocks the phone page | Documented; the Phone screen shows the URL so users can tell it's a network issue. |
| Sony resets the screensaver when Ambient settings are opened | Repaired on boot and on Home resume (section 8). |
| Low disk on the development Mac (2.8 GB free) | Build in CI if needed; skip the emulator images. |
