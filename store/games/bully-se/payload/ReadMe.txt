SilentPatch for Bully: Scholarship Edition
Build 3 BETA
Last update - 07.03.2020
Co-developed by P3ti

WARNING - THIS IS A BETA RELEASE
IF YOU ENCOUNTER CRASHES, REFER TO "SUBMITTING FEEDBACK" SECTION BELOW IN THE FILE


DESCRIPTION

	This game, which shares a lot of the internals with GTA games, performs fairly well in its PC incarnation as is.
	However, it's more than likely that you have at some point spotted the amount of complaints Windows 10
	users have about the game, or maybe you have encountered crashes yourself.

	SilentPatch attempts to fix Bully memory management completely, so it behaves in the same way independent
	of Windows version. This is not the only fix included, however - most notably, it attempts to improve
	gameplay experience by improving frame pacing, as well as fixing a few other issues.

	Fixes featured in this plugin:

	CRASH AND BUG FIXES:
		* Collision loading code has been improved, fixing occasional crashes on initial game load
		* Fixed game's objects pool usage, fixing possible crashes
		* Fixed an occasional crash when starting Nutcrackin' or Music Class
		* Fixed numerous instances of memory corruption on game exit
		* Fixed an use-after-free in sound streaming code, causing a rare crash when talking to people
		* Fixed handle leaks in audio code, preventing handles from accumulating during the game
		* Fixed several memory leaks in audio code, preventing out of memory crashes during extended play sessions
		* Made memory manager workarounds toggleable via the INI file - disabled by default, to be removed in the future
		* Frame Limiter has been made much more precise, so the game should lock at exactly 30FPS now
		  (as opposed to stock limiter being prone to dropping frames a lot)
		* Fixed an issue where game would use more CPU than required when minimized

	QUALITY OF LIFE IMPROVEMENTS:
		* An option to change FPS cap has been added to SilentPatchBully.ini file (game defaults to 30FPS)
		* FILE_FLAG_NO_BUFFERING flag has been removed from IMG reading functions - potentially speeding
		  up streaming


INSTALLATION

	Easy as pie. You only need to extract archive contents to your Bully: Scholarship Edition directory.
	You can use the INI file to configure some patch properties, make sure you check it before
	you try out the patch!


SUBMITTING FEEDBACK

	Since this is a public beta release, you may encounter crashes. Because of this, MiniDumper utility has
	been shipped together with SilentPatch. In case of a crash, a .dmp file will be created in your game directory.

	If you want to report it as a bug (any feedback is very much appreciated), first ENSURE YOU HAVE AN UNMODDED GAME
	(texture mods are fine, scripts - not so much). You can report a bug (.dmp file + a brief explanation on what
	you were doing when the game crashes) in any of those places:

	* SilentPatchBully issues page on GitHub:
	  https://github.com/CookiePLMonster/SilentPatchBully/issues
	* #silentpatch channel in RockstarVision Discord:
	  https://discord.gg/VJSxY5e


CREDITS

	P3ti (https://github.com/P3ti) - co-developer
	amzy (https://twitch.tv/amzy) - testing, overall support


SUPPORTERS

	TWIST_OF_HATE


CONTACT

	zdanio95@gmail.com - e-mail
	Silent#1222 - Discord

Subscribe to my YouTube channel for more footage from my mods!
https://www.youtube.com/user/CookiePLMonster

Follow my Twitter account to be up to all my mods updates!
http://twitter.com/__silent_

Also take a look at my blog, featuring modding and programming related articles and more!
https://cookieplmonster.github.io/