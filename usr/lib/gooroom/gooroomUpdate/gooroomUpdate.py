#!/usr/bin/python2.7 -E
#-*- coding:utf-8 -*-

try:
    import os
    import stat
    import commands
    import codecs
    import datetime
    import sys
    import string
    import tempfile
    import threading
    import time
    import gettext
    import fnmatch
    import urllib2
    import io
    import tarfile
    import re
    import apt
    from user import home
    from sets import Set
    import proxygsettings
    sys.path.append('/usr/lib/gooroom/common')
    from configobj import ConfigObj
except Exception, detail:
    print detail
    pass

try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')

    from gi.repository import Gtk
    from gi.repository import Gdk
    from gi.repository import GObject
    from gi.repository import GdkPixbuf
except Exception, detail:
    print detail
    pass

from subprocess import Popen, PIPE

try:
    numGooroomUpdate = commands.getoutput("ps -A | grep gooroomUpdate | wc -l")
    if (numGooroomUpdate != "0"):
        os.system("killall gooroomUpdate")
except Exception, detail:
    print detail

architecture = commands.getoutput("uname -a")
if (architecture.find("x86_64") >= 0):
    import ctypes
    libc = ctypes.CDLL('libc.so.6')
    libc.prctl(15, 'gooroomUpdate', 0, 0, 0)
else:
    import dl
    if os.path.exists('/lib/libc.so.6'):
        libc = dl.open('/lib/libc.so.6')
        libc.call('prctl', 15, 'gooroomUpdate', 0, 0, 0)
    elif os.path.exists('/lib/i386-linux-gnu/libc.so.6'):
        libc = dl.open('/lib/i386-linux-gnu/libc.so.6')
        libc.call('prctl', 15, 'gooroomUpdate', 0, 0, 0)

# i18n
gettext.install("gooroom-update", "/usr/share/gooroom/locale")

CONFIG_DIR = "%s/.config/gooroom" % home

(TAB_UPDATES, TAB_UPTODATE, TAB_ERROR) = range(3)

package_short_descriptions = {}
package_descriptions = {}

(UPDATE_CHECKED, UPDATE_ALIAS, UPDATE_LEVEL_PIX, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_LEVEL_STR, UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ) = range(13)

class Alias():
    def __init__(self, name, short_description, description):

        name = name.strip()
        short_description = short_description.strip()
        description = description.strip()

        if (name.startswith('_("') and name.endswith('")')):
            name = _(name[3:-2])
        if (short_description.startswith('_("') and short_description.endswith('")')):
            short_description = _(short_description[3:-2])
        if (description.startswith('_("') and description.endswith('")')):
            description = _(description[3:-2])

        self.name = name
        self.short_description = short_description
        self.description = description

class PackageUpdate():
    def __init__(self, source_package_name, level, oldVersion, newVersion, extraInfo, warning, update_type, origin, tooltip):
        self.name = source_package_name
        self.description = ""
        self.short_description = ""
        self.level = level
        self.oldVersion = oldVersion
        self.newVersion = newVersion
        self.size = 0
        self.extraInfo = extraInfo
        self.warning = warning
        self.type = update_type
        self.origin = origin
        self.tooltip = tooltip
        self.packages = []
        self.alias = source_package_name

    def add_package(self, package, size, short_description, description):
        self.packages.append(package)
        self.description = description
        self.short_description = short_description
        self.size += size

class ChangelogRetriever(threading.Thread):
    def __init__(self, package_update, wTree):
        threading.Thread.__init__(self)
        self.source_package = package_update.name
        self.level = package_update.level
        self.version = package_update.newVersion
        self.origin = package_update.origin
        self.wTree = wTree
        # get the proxy settings from gsettings
        self.ps = proxygsettings.get_proxy_settings()


        # Remove the epoch if present in the version
        if ":" in self.version:
            self.version = self.version.split(":")[-1]

    def get_gooroom_source(self):
        sources = "/etc/apt/sources.list.d/official-package-repositories.list"
        with open(sources, "r") as f:
            for line in f.readlines():
                if "gooroom" in line:
                    source = line.split()[1]
                    break
            else:
                return

        return source

    def get_gooroom_changelog(self, source):
        max_size = 1000000
        if self.source_package.startswith("lib"):
            abbr = self.source_package[0:4]
        else:
            abbr = self.source_package[0]

        common_uri = "%s/pool/main/%s/%s/" % (source, abbr, self.source_package)
        dsc_uri = common_uri + "%s_%s.dsc" % (self.source_package, self.version)
        try:
            dsc = urllib2.urlopen(dsc_uri, None, 10).read().decode("utf-8")
        except Exception as e:
            print "Could not open gooroom URL %s - %s" % (dsc_uri, e)
            return

        filename = None
        debian_flag = None
        for line in dsc.split("\n"):
            if ".tar" in line:
                tarball_line = line.strip().split(" ", 2)
                if len(tarball_line) == 3:
                    checksum, size, filename = tarball_line
                    debian_flag = False
                    break

        for line in dsc.split("\n"):
            if "debian.tar" in line:
                tarball_line = line.strip().split(" ", 2)
                if len(tarball_line) == 3:
                    checksum, size, filename = tarball_line
                    debian_flag = True
                    break

        if not filename or not size or not size.isdigit():
            print "Unsupported debian .dsc file format. Skipping this package."
            return

        if (int(size) > max_size):
            print "Skipping download"
            return

        file_uri = common_uri + filename
        try:
            deb_file = urllib2.urlopen(file_uri, None, 10).read()
        except Exception as e:
            print "Could not download tarball from : %s - %s" % (file_uri, e)
            return

        if filename.endswith(".xz"):
            cmd = ["xz", "--decompress"]
            try:
                xz = Popen(cmd, stdin=PIPE, stdout=PIPE)
                deb_file, deb_err = xz.communicate(deb_file)
            except EnvironmentError as e:
                print "Error encountered while decompressing xz file : %s" % e
                return

        deb_file = io.BytesIO(deb_file)
        try:
            with tarfile.open(fileobj = deb_file) as tf:
                if debian_flag:
                    deb_changelog = tf.extractfile("debian/changelog").read()
                else:
                    deb_changelog = tf.extractfile("%s-%s/debian/changelog" % (self.source_package, self.version)).read()
        except tarfile.TarError as e:
            print "Error encountered while reading tarball : %s" % e
            return

        return deb_changelog

    def run(self):
        Gdk.threads_enter()
        self.wTree.get_object("textview_changes").get_buffer().set_text(_("Downloading changelog..."))
        Gdk.threads_leave()

        if self.ps == {}:
            # use default urllib2 proxy mechanisms (possibly *_proxy environment vars)
            proxy = urllib2.ProxyHandler()
        else:
            # use proxy settings retrieved from gsettings
            proxy = urllib2.ProxyHandler(self.ps)

        opener = urllib2.build_opener(proxy)
        urllib2.install_opener(opener)

        source = self.get_gooroom_source()
        changelog = _("No changelog available")

        changelog_sources = []
        if self.origin == "gooroom":
            if (self.source_package.startswith("lib")):
                changelog_sources.append("%s/pool/main/%s/%s/%s_%s.changelog" % (source, self.source_package[0:4], self.source_package, self.source_package, self.version))
            else:
                changelog_sources.append("%s/pool/main/%s/%s/%s_%s.changelog" % (source, self.source_package[0], self.source_package, self.source_package, self.version))
        elif self.origin == "debian":
            if (self.source_package.startswith("lib")):
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/main/%s/%s/%s_%s_changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/contrib/%s/%s/%s_%s_changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/non-free/%s/%s/%s_%s_changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
            else:
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/main/%s/%s/%s_%s_changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/contrib/%s/%s/%s_%s_changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/non-free/%s/%s/%s_%s_changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))

        for changelog_source in changelog_sources:
            try:
                print "Trying to fetch the changelog from: %s" % changelog_source
                url = urllib2.urlopen(changelog_source, None, 10)
                source = url.read()
                url.close()

                changelog = ""
                changelog = source
                break
            except:
                if self.origin == "gooroom":
                    output = self.get_gooroom_changelog(source)
                    if output:
                        changelog = output

        Gdk.threads_enter()
        self.wTree.get_object("textview_changes").get_buffer().set_text(changelog)
        Gdk.threads_leave()

class AutomaticRefreshThread(threading.Thread):
    def __init__(self, treeView, statusIcon, wTree):
        threading.Thread.__init__(self)
        self.treeView = treeView
        self.statusIcon = statusIcon
        self.wTree = wTree

    def run(self):
        global app_hidden
        global log
        try:
            while(True):
                prefs = read_configuration()
                timer = (prefs["timer_minutes"] * 60) + (prefs["timer_hours"] * 60 * 60) + (prefs["timer_days"] * 24 * 60 * 60)

                try:
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Auto-refresh timer is going to sleep for " + str(prefs["timer_minutes"]) + " minutes, " + str(prefs["timer_hours"]) + " hours and " + str(prefs["timer_days"]) + " days\n")
                    log.flush()
                except:
                    pass # cause it might be closed already
                timetosleep = int(timer)
                if (timetosleep == 0):
                    time.sleep(60) # sleep 1 minute, don't mind the config we don't want an infinite loop to go nuts :)
                else:
                    time.sleep(timetosleep)
                    if (app_hidden == True):
                        try:
                            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ GooroomUpdate is in tray mode, performing auto-refresh\n")
                            log.flush()
                        except:
                            pass # cause it might be closed already
                        # Refresh
                        refresh = RefreshThread(self.treeView, self.statusIcon, self.wTree, root_mode=True)
                        refresh.start()
                    else:
                        try:
                            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ The gooroomUpdate window is open, skipping auto-refresh\n")
                            log.flush()
                        except:
                            pass # cause it might be closed already

        except Exception, detail:
            try:
                log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Exception occurred in the auto-refresh thread.. so it's probably dead now: " + str(detail) + "\n")
                log.flush()
            except:
                pass # cause it might be closed already

class InstallThread(threading.Thread):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    def __init__(self, treeView, statusIcon, wTree):
        threading.Thread.__init__(self)
        self.treeView = treeView
        self.statusIcon = statusIcon
        self.wTree = wTree

    def run(self):
        global log
        try:
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Install requested by user\n")
            log.flush()
            Gdk.threads_enter()
            self.wTree.get_object("window").get_window().set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
            self.wTree.get_object("window").set_sensitive(False)
            installNeeded = False
            packages = []
            model = self.treeView.get_model()
            Gdk.threads_leave()

            iter = model.get_iter_first()
            while (iter != None):
                checked = model.get_value(iter, UPDATE_CHECKED)
                if (checked == "true"):
                    installNeeded = True
                    package_update = model.get_value(iter, UPDATE_OBJ)
                    for package in package_update.packages:
                        packages.append(package)
                        log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Will install " + str(package) + "\n")
                        log.flush()
                iter = model.iter_next(iter)

            if (installNeeded == True):
                proceed = True
                try:
                    pkgs = ' '.join(str(pkg) for pkg in packages)
                    warnings = commands.getoutput("/usr/lib/gooroom/gooroomUpdate/checkWarnings.py %s" % pkgs)
                    #print ("/usr/lib/gooroom/gooroomUpdate/checkWarnings.py %s" % pkgs)
                    warnings = warnings.split("###")
                    if len(warnings) == 2:
                        installations = warnings[0].split()
                        removals = warnings[1].split()
                        if len(installations) > 0 or len(removals) > 0:
                            Gdk.threads_enter()
                            try:
                                dialog = Gtk.Dialog("", transient_for=None, modal=True, destroy_with_parent=True)
                                dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
                                dialog.set_icon_name("gooroomupdater")
                                dialog.set_default_size(320, 400)
                                dialog.set_resizable(True)

                                markup = Gtk.Label()
                                markup.set_markup("<b>" + _("This upgrade will trigger additional changes") + "</b>")
                                markup.set_margin_top(10)
                                markup.set_margin_bottom(45)

                                if len(removals) > 0:
                                    # Removals
                                    label = Gtk.Label()
                                    if len(removals) == 1:
                                        label.set_text(_("The following package will be removed:"))
                                    else:
                                        label.set_text(_("The following %d packages will be removed:") % len(removals))
                                    #label.set_alignment(0.1, 0.5)
                                    label.set_halign(Gtk.Align.START)
                                    label.set_margin_start(7)
                                    scrolledWindow = Gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                                    scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType._AUTOMATIC)
                                    scrolledWindow.set_margin_bottom(5)
                                    scrolledWindow.set_margin_start(5)
                                    scrolledWindow.set_margin_end(5)
                                    treeview = Gtk.TreeView()
                                    column1 = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)
                                    treeview.set_margin_start(1)
                                    treeview.set_margin_end(1)
                                    treeview.set_margin_top(1)
                                    model = Gtk.TreeStore(str)
                                    removals.sort()
                                    for pkg in removals:
                                        iter = model.insert_before(None, None)
                                        model.set_value(iter, 0, pkg)
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)
                                    dialog.vbox.pack_start(markup, False, False, 0)
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)

                                if len(installations) > 0:
                                    # Installations
                                    label = Gtk.Label()
                                    if len(installations) == 1:
                                        label.set_text(_("The following package will be installed:"))
                                    else:
                                        label.set_text(_("The following %d packages will be installed:") % len(installations))
                                    #label.set_alignment(0.1, 0.5)
                                    label.set_halign(Gtk.Align.START)
                                    label.set_margin_start(7)
                                    scrolledWindow = Gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                                    scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                                    scrolledWindow.set_margin_bottom(5)
                                    scrolledWindow.set_margin_start(5)
                                    scrolledWindow.set_margin_end(5)
                                    treeview = Gtk.TreeView()
                                    column1 = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)
                                    treeview.set_margin_start(1)
                                    treeview.set_margin_end(1)
                                    treeview.set_margin_top(1)
                                    treeview.set_margin_bottom(1)
                                    model = Gtk.TreeStore(str)
                                    installations.sort()
                                    for pkg in installations:
                                        iter = model.insert_before(None, None)
                                        model.set_value(iter, 0, pkg)
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)
                                    dialog.vbox.pack_start(markup, False, False, 0)
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)

                                dialog.show_all()
                                if dialog.run() == Gtk.ResponseType.OK:
                                    proceed = True
                                else:
                                    proceed = False
                                dialog.destroy()
                            except Exception, detail:
                                print detail
                            Gdk.threads_leave()
                        else:
                            proceed = True
                except Exception, details:
                    print details

                if proceed:
                    Gdk.threads_enter()
                    self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_apply))
                    self.statusIcon.set_tooltip_text(_("Installing updates"))
                    self.statusIcon.set_visible(True)
                    Gdk.threads_leave()
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Ready to launch synaptic\n")
                    log.flush()
                    cmd = ["pkexec", "/usr/sbin/synaptic-script.sh", "--hide-main-window",  \
                           "--non-interactive", "--parent-window-id", "%s" % self.wTree.get_object("window").get_window().get_xid()]
                    cmd.append("-o")
                    cmd.append("Synaptic::closeZvt=true")
                    cmd.append("--progress-str")
                    cmd.append("\"" + _("Please wait, this can take some time") + "\"")
                    cmd.append("--finish-str")
                    cmd.append("\"" + _("Update is complete") + "\"")

                    pkg_list = ""
                    cmd.append("--set-selections-file")
                    for pkg in packages:
                        pkg_list += "{},".format(pkg)
                    pkg_list[:-1]

                    cmd.append(pkg_list)
                    comnd = Popen(' '.join(cmd), stdout=log, stderr=log, shell=True)
                    returnCode = comnd.wait()
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Return code:" + str(returnCode) + "\n")
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Install finished\n")
                    log.flush()

                    if "gooroom-update" in packages or "gooroom-upgrade-info" in packages:
                        # Restart
                        try:
                            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Gooroomupdate was updated, restarting it...\n")
                            log.flush()
                            log.close()
                        except:
                            pass #cause we might have closed it already

                        command = "/usr/lib/gooroom/gooroomUpdate/gooroomUpdate.py &"
                        os.system(command)

                    else:
                        # Refresh
                        Gdk.threads_enter()
                        self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_busy))
                        self.statusIcon.set_tooltip_text(_("Checking for updates"))
                        self.wTree.get_object("window").get_window().set_cursor(None)
                        self.wTree.get_object("window").set_sensitive(True)
                        Gdk.threads_leave()
                        refresh = RefreshThread(self.treeView, self.statusIcon, self.wTree)
                        refresh.start()
                else:
                    # Stop the blinking but don't refresh
                    Gdk.threads_enter()
                    self.wTree.get_object("window").get_window().set_cursor(None)
                    self.wTree.get_object("window").set_sensitive(True)
                    Gdk.threads_leave()
                    # FIXME: thread가 끝나면 업데이트가 함께 죽어버림
                    # 예제에서 참고하여 아래와 같이 수정함.
                    time.sleep(0.2)
            else:
                # Stop the blinking but don't refresh
                Gdk.threads_enter()
                self.wTree.get_object("window").get_window().set_cursor(None)
                self.wTree.get_object("window").set_sensitive(True)
                Gdk.threads_leave()
                time.sleep(0.2)

        except Exception, detail:
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Exception occurred in the install thread: " + str(detail) + "\n")
            log.flush()
            Gdk.threads_enter()
            self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_error))
            self.statusIcon.set_tooltip_text(_("Could not install the security updates"))
            self.statusIcon.set_visible(True)
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Could not install security updates\n")
            log.flush()
            #self.statusIcon.set_blinking(False)
            self.wTree.get_object("window").get_window().set_cursor(None)
            self.wTree.get_object("window").set_sensitive(True)
            Gdk.threads_leave()
            time.sleep(0.2)

class AutoInstallScheduleThread(threading.Thread):
    def __init__(self, treeView, statusIcon, wTree):
        threading.Thread.__init__(self)
        self.treeView = treeView
        self.statusIcon = statusIcon
        self.wTree = wTree

    def run(self):
        import time
        while(1):
            prefs = read_configuration()
            pref_date= prefs['auto_upgrade_date']
            pref_time= prefs['auto_upgrade_time']
            cur_date= int(time.strftime("%w"))+1
            cur_time= int(time.strftime("%H"))
            cur_min= int(time.strftime("%M"))
            hit_date= False
            hit_time= False

            if pref_date== 0 or pref_date== cur_date:
                hit_date= True
            if pref_time== pref_time and cur_min== 0:
                hit_time= True

            if hit_date and hit_time and prefs['auto_upgrade']:
                autoinst= AutoInstallThread(self.treeView, self.statusIcon, self.wTree)
                autoinst.start()
            time.sleep(60)

class AutoInstallThread(threading.Thread):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    def __init__(self, treeView, statusIcon, wTree):
        threading.Thread.__init__(self)
        self.treeView = treeView
        self.statusIcon = statusIcon
        self.wTree = wTree

    def run(self):
        global log
        try:
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Auto Install requested by user\n")
            log.flush()
            Gdk.threads_enter()
            self.wTree.get_object("window").get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
            self.wTree.get_object("window").set_sensitive(False)
            installNeeded = False
            packages = []
            model = self.treeView.get_model()
            Gdk.threads_leave()

            iter = model.get_iter_first()
            while (iter != None):
                checked = model.get_value(iter, UPDATE_CHECKED)
                if (checked == "true"):
                    installNeeded = True
                    package_update = model.get_value(iter, UPDATE_OBJ)
                    for package in package_update.packages:
                        packages.append(package)
                        log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Will install " + str(package) + "\n")
                        log.flush()
                iter = model.iter_next(iter)

            if (installNeeded == True):
                proceed = True
                try:
                    pkgs = ' '.join(str(pkg) for pkg in packages)
                    warnings = commands.getoutput("/usr/lib/gooroom/gooroomUpdate/checkWarnings.py %s" % pkgs)
                    #print ("/usr/lib/gooroom/gooroomUpdate/checkWarnings.py %s" % pkgs)
                    warnings = warnings.split("###")
                    if len(warnings) == 2:
                        installations = warnings[0].split()
                        removals = warnings[1].split()
                        if len(installations) > 0 or len(removals) > 0:
                            Gdk.threads_enter()
                            try:
                                dialog = Gtk.MessageDialog(None, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.WARNING, Gtk.ButtonsType.OK_CANCEL, None)
                                dialog.set_title("")
                                dialog.set_markup("<b>" + _("This upgrade will trigger additional changes") + "</b>")
                                #dialog.format_secondary_markup("<i>" + _("All available upgrades for this package will be ignored.") + "</i>")
                                dialog.set_icon_name("gooroomupdater")
                                dialog.set_default_size(320, 400)
                                dialog.set_resizable(True)

                                if len(removals) > 0:
                                    # Removals
                                    label = Gtk.Label()
                                    if len(removals) == 1:
                                        label.set_text(_("The following package will be removed:"))
                                    else:
                                        label.set_text(_("The following %d packages will be removed:") % len(removals))
                                    label.set_alignment(0, 0.5)
                                    scrolledWindow = Gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                                    scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                                    treeview = Gtk.TreeView()
                                    column1 = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)
                                    model = Gtk.TreeStore(str)
                                    removals.sort()
                                    for pkg in removals:
                                        iter = model.insert_before(None, None)
                                        model.set_value(iter, 0, pkg)
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)

                                if len(installations) > 0:
                                    # Installations
                                    label = Gtk.Label()
                                    if len(installations) == 1:
                                        label.set_text(_("The following package will be installed:"))
                                    else:
                                        label.set_text(_("The following %d packages will be installed:") % len(installations))
                                    label.set_alignment(0, 0.5)
                                    scrolledWindow = Gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                                    scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                                    treeview = Gtk.TreeView()
                                    column1 = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)
                                    model = Gtk.TreeStore(str)
                                    installations.sort()
                                    for pkg in installations:
                                        iter = model.insert_before(None, None)
                                        model.set_value(iter, 0, pkg)
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)

                                dialog.show_all()
                                if dialog.run() == Gtk.ResponseType.OK:
                                    proceed = True
                                else:
                                    proceed = False
                                dialog.destroy()
                            except Exception, detail:
                                print detail
                            Gdk.threads_leave()
                        else:
                            proceed = True
                except Exception, details:
                    print details

                if proceed:
                    Gdk.threads_enter()
                    self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_apply))
                    self.statusIcon.set_tooltip_text(_("Installing updates"))
                    self.statusIcon.set_visible(True)
                    Gdk.threads_leave()
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Ready to launch synaptic\n")
                    log.flush()
                    cmd = ["pkexec", "/usr/lib/gooroom/gooroomUpdate/autoInstall.py"]
                    for pkg in packages:
                        cmd.append(pkg)
                    comnd = Popen(' '.join(cmd), stdout=log, stderr=log, shell=True)
                    returnCode = comnd.wait()
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Return code:" + str(returnCode) + "\n")
                    #sts = os.waitpid(comnd.pid, 0)
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Install finished\n")
                    log.flush()

                    if "gooroom-update" in packages or "gooroom-upgrade-info" in packages:
                        # Restart
                        try:
                            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Gooroomupdate was updated, restarting it...\n")
                            log.flush()
                            log.close()
                        except:
                            pass #cause we might have closed it already

                        command = "/usr/lib/gooroom/gooroomUpdate/gooroomUpdate.py &"
                        os.system(command)

                    else:
                        # Refresh
                        Gdk.threads_enter()
                        self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_busy))
                        self.statusIcon.set_tooltip_text(_("Checking for updates"))
                        self.wTree.get_object("window").get_window().set_cursor(None)
                        self.wTree.get_object("window").set_sensitive(True)
                        Gdk.threads_leave()
                        refresh = RefreshThread(self.treeView, self.statusIcon, self.wTree)
                        refresh.start()
                else:
                    # Stop the blinking but don't refresh
                    Gdk.threads_enter()
                    self.wTree.get_object("window").get_window().set_cursor(None)
                    self.wTree.get_object("window").set_sensitive(True)
                    Gdk.threads_leave()
            else:
                # Stop the blinking but don't refresh
                Gdk.threads_enter()
                self.wTree.get_object("window").get_window().set_cursor(None)
                self.wTree.get_object("window").set_sensitive(True)
                Gdk.threads_leave()

        except Exception, detail:
            print(detail)
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Exception occurred in the install thread: " + str(detail) + "\n")
            log.flush()
            Gdk.threads_enter()
            self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_error))
            self.statusIcon.set_tooltip_text(_("Could not install the security updates"))
            self.statusIcon.set_visible(True)
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Could not install security updates\n")
            log.flush()
            #self.statusIcon.set_blinking(False)
            self.wTree.get_object("window").get_window().set_cursor(None)
            self.wTree.get_object("window").set_sensitive(True)
            Gdk.threads_leave()

class RefreshThread(threading.Thread):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global statusbar
    global context_id

    def __init__(self, treeview_update, statusIcon, wTree, root_mode=False):
        threading.Thread.__init__(self)
        self.treeview_update = treeview_update
        self.statusIcon = statusIcon
        self.wTree = wTree
        self.root_mode = root_mode

    def fetch_l10n_descriptions(self, package_names):
        if os.path.exists("/var/lib/apt/lists"):
            try:
                super_buffer = []
                for file in os.listdir("/var/lib/apt/lists"):
                    if ("i18n_Translation") in file and not file.endswith("Translation-en"):
                        fd = codecs.open(os.path.join("/var/lib/apt/lists", file), "r", "utf-8")
                        super_buffer += fd.readlines()

                i = 0
                while i < len(super_buffer):
                    line = super_buffer[i].strip()
                    if line.startswith("Package: "):
                        try:
                            pkgname = line.replace("Package: ", "")
                            short_description = ""
                            description = ""
                            j = 2 # skip md5 line after package name line
                            while True:
                                if (i+j >= len(super_buffer)):
                                    break
                                line = super_buffer[i+j].strip()
                                if line.startswith("Package: "):
                                    break
                                if j==2:
                                    short_description = line
                                else:
                                    description += "\n" + line
                                j += 1
                            if pkgname in package_names:
                                if not package_descriptions.has_key(pkgname):
                                    package_short_descriptions[pkgname] = short_description
                                    package_descriptions[pkgname] = description
                        except Exception, detail:
                            print "a %s" % detail
                    i += 1
                del super_buffer
            except Exception, detail:
                print "Could not fetch l10n descriptions.."
                print detail

    def run(self):
        global log
        global app_hidden
        global alert
        global alertDialog
        Gdk.threads_enter()
        vpaned_position = wTree.get_object("vpaned1").get_position()
        Gdk.threads_leave()

        try:
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Starting refresh\n")
            log.flush()
            Gdk.threads_enter()
            statusbar.push(context_id, _("Starting refresh..."))
            self.wTree.get_object("notebook_status").set_current_page(TAB_UPDATES)
            self.wTree.get_object("window").get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
            self.wTree.get_object("window").set_sensitive(False)

            prefs = read_configuration()

            # Starts the blinking
            self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_busy))
            self.statusIcon.set_tooltip_text(_("Checking for updates"))
            wTree.get_object("vpaned1").set_position(vpaned_position)
            #self.statusIcon.set_blinking(True)
            Gdk.threads_leave()

            model = Gtk.TreeStore(str, str, GdkPixbuf.Pixbuf, str, str, str, int, str, GdkPixbuf.Pixbuf, str, str, str, object)
            # UPDATE_CHECKED, UPDATE_ALIAS, UPDATE_LEVEL_PIX, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_LEVEL_STR,
            # UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ

            model.set_sort_column_id( UPDATE_SORT_STR, Gtk.SortType.ASCENDING )

            # Check to see if no other APT process is running
            if self.root_mode:
                p1 = Popen(['ps', '-U', 'root', '-o', 'comm'], stdout=PIPE)
                p = p1.communicate()[0]
                running = False
                pslist = p.split('\n')
                for process in pslist:
                    if process.strip() in ["dpkg", "apt-get","synaptic","update-manager", "adept", "adept-notifier"]:
                        running = True
                        break

                if (running == True):
                    Gdk.threads_enter()
                    self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_unknown))
                    self.statusIcon.set_tooltip_text(_("Another application is using APT"))
                    statusbar.push(context_id, _("Another application is using APT"))
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Another application is using APT\n")
                    log.flush()
                    #self.statusIcon.set_blinking(False)
                    self.wTree.get_object("window").get_window().set_cursor(None)
                    self.wTree.get_object("window").set_sensitive(True)
                    Gdk.threads_leave()
                    return False

            Gdk.threads_enter()
            statusbar.push(context_id, _("Finding the list of updates..."))
            wTree.get_object("vpaned1").set_position(vpaned_position)
            Gdk.threads_leave()
            if app_hidden:
                refresh_command = "/usr/lib/gooroom/gooroomUpdate/checkAPT.py 2>/dev/null"
            else:
                refresh_command = "/usr/lib/gooroom/gooroomUpdate/checkAPT.py --use-synaptic %s 2>/dev/null" % self.wTree.get_object("window").get_window().get_xid()
            if self.root_mode:
                refresh_command = "pkexec %s" % refresh_command
            updates =  commands.getoutput(refresh_command)

            # Look for gooroom-update
            if ("UPDATE###gooroom-update###" in updates or "UPDATE###gooroom-upgrade-info###" in updates):
                new_gooroomupdate = True
            else:
                new_gooroomupdate = False

            updates = string.split(updates, "---EOL---")

            # Look at the updates one by one
            package_updates = {}
            package_names = Set()
            num_visible = 0
            num_safe = 0
            download_size = 0
            num_ignored = 0

            if (len(updates) == None):
                Gdk.threads_enter()
                is_enable_tools = False
                self.wTree.get_object("notebook_status").set_current_page(TAB_UPTODATE)
                self.statusIcon.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_up2date))
                self.statusIcon.set_tooltip_text(_("Your system is up to date"))
                statusbar.push(context_id, _("Your system is up to date"))
                log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ System is up to date\n")
                log.flush()
                Gdk.threads_leave()
            else:
                for pkg in updates:
                    if pkg.startswith("CHECK_APT_ERROR"):
                        try:
                            error_msg = updates[1]
                        except:
                            error_msg = ""
                        Gdk.threads_enter()
                        self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_error))
                        self.statusIcon.set_tooltip_text("%s\n\n%s" % (_("Could not refresh the list of updates"), error_msg))
                        self.statusIcon.set_visible(True)
                        statusbar.push(context_id, _("Could not refresh the list of updates"))
                        log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Error in checkAPT.py, could not refresh the list of updates\n")

                        log.flush()
                        self.wTree.get_object("notebook_status").set_current_page(TAB_ERROR)
                        self.wTree.get_object("label_error_details").set_markup("<b>%s</b>" % error_msg)
                        self.wTree.get_object("label_error_details").show()
                        #self.statusIcon.set_blinking(False)
                        self.wTree.get_object("window").get_window().set_cursor(None)
                        self.wTree.get_object("window").set_sensitive(True)
                        #statusbar.push(context_id, _(""))
                        Gdk.threads_leave()
                        return False

                    values = string.split(pkg, "###")
                    print(pkg)
                    if len(values) == 10:
                        status = values[0]
                        package = values[1]
                        newVersion = values[2]
                        oldVersion = values[3]
                        size = int(values[4])
                        source_package = values[5]
                        update_type = values[6]
                        origin = values[7]
                        short_description = values[8]
                        description = values[9]

                        package_names.add(package.replace(":i386", "").replace(":amd64", ""))

                        if not package_updates.has_key(source_package):
                            security_update = (update_type == "security")

                            if update_type == "security":
                                tooltip = _("Security update")
                            elif update_type == "backport":
                                tooltip = _("Software backport. Be careful when upgrading. New versions of software can introduce regressions.")
                            elif update_type == "unstable":
                                tooltip = _("Unstable software. Only apply this update to help developers beta-test new software.")
                            else:
                                tooltip = _("Software update")

                            extraInfo = ""
                            warning = ""
                            level = 3 # Level 3 by default
                            if update_type == "gooroom":
                                level = 1 # Level 1 by default
                                update_type = "package"
                            if origin == "debian":
                                level = 2
                            rulesFile = open("/usr/lib/gooroom/gooroomUpdate/rules","r")
                            rules = rulesFile.readlines()
                            goOn = True
                            foundPackageRule = False # whether we found a rule with the exact package name or not
                            for rule in rules:
                                if (goOn == True):
                                    rule_fields = rule.split("|")
                                    if (len(rule_fields) == 5):
                                        rule_package = rule_fields[0]
                                        rule_version = rule_fields[1]
                                        rule_level = rule_fields[2]
                                        rule_extraInfo = rule_fields[3]
                                        rule_warning = rule_fields[4]
                                        if (rule_package == source_package):
                                            foundPackageRule = True
                                            if (rule_version == newVersion):
                                                level = rule_level
                                                extraInfo = rule_extraInfo
                                                warning = rule_warning
                                                goOn = False # We found a rule with the exact package name and version, no need to look elsewhere
                                            else:
                                                if (rule_version == "*"):
                                                    level = rule_level
                                                    extraInfo = rule_extraInfo
                                                    warning = rule_warning
                                        else:
                                            if (rule_package.startswith("*")):
                                                keyword = rule_package.replace("*", "")
                                                index = source_package.find(keyword)
                                                if (index > -1 and foundPackageRule == False):
                                                    level = rule_level
                                                    extraInfo = rule_extraInfo
                                                    warning = rule_warning
                            rulesFile.close()
                            level = int(level)

                            # Create a new Update
                            update = PackageUpdate(source_package, level, oldVersion, newVersion, extraInfo, warning, update_type, origin, tooltip)
                            update.add_package(package, size, short_description, description)
                            package_updates[source_package] = update
                        else:
                            # Add the package to the Update
                            update = package_updates[source_package]
                            update.add_package(package, size, short_description, description)

                self.fetch_l10n_descriptions(package_names)

                for source_package in package_updates.keys():
                    package_update = package_updates[source_package]
                    if (new_gooroomupdate and package_update.name != "gooroom-update" and package_update.name != "gooroom-upgrade-info"):
                        continue

                    # l10n descriptions
                    l10n_descriptions(package_update)
                    package_update.short_description = clean_l10n_short_description(package_update.short_description)
                    package_update.description = clean_l10n_description(package_update.description)

                    security_update = (package_update.type == "security")

                    if ((prefs["level" + str(package_update.level) + "_visible"]) or (security_update and prefs['security_visible'])):
                        iter = model.insert_before(None, None)
                        if (security_update and prefs['security_safe']):
                            model.set_value(iter, UPDATE_CHECKED, "true")
                            num_safe = num_safe + 1
                            download_size = download_size + package_update.size
                        elif (prefs["level" + str(package_update.level) + "_safe"]):
                            model.set_value(iter, UPDATE_CHECKED, "true")
                            num_safe = num_safe + 1
                            download_size = download_size + package_update.size
                        else:
                            model.set_value(iter, UPDATE_CHECKED, "false")

                        model.row_changed(model.get_path(iter), iter)

                        shortdesc = package_update.short_description
                        if len(shortdesc) > 100:
                            shortdesc = shortdesc[:100] + "..."
                        if (prefs["descriptions_visible"]):
                            model.set_value(iter, UPDATE_ALIAS, package_update.alias + "\n<small><span foreground='#5C5C5C'>%s</span></small>" % shortdesc)
                        else:
                            model.set_value(iter, UPDATE_ALIAS, package_update.alias)
                        model.set_value(iter, UPDATE_LEVEL_PIX, GdkPixbuf.Pixbuf.new_from_file("/usr/lib/gooroom/gooroomUpdate/icons/level" + str(package_update.level) + ".png"))
                        model.set_value(iter, UPDATE_OLD_VERSION, package_update.oldVersion)
                        model.set_value(iter, UPDATE_NEW_VERSION, package_update.newVersion)
                        model.set_value(iter, UPDATE_LEVEL_STR, str(package_update.level))
                        model.set_value(iter, UPDATE_SIZE, package_update.size)
                        model.set_value(iter, UPDATE_SIZE_STR, size_to_string(package_update.size))
                        model.set_value(iter, UPDATE_TYPE_PIX, GdkPixbuf.Pixbuf.new_from_file("/usr/lib/gooroom/gooroomUpdate/icons/update-type-%s.png" % package_update.type))
                        model.set_value(iter, UPDATE_TYPE, package_update.type)
                        model.set_value(iter, UPDATE_TOOLTIP, package_update.tooltip)
                        model.set_value(iter, UPDATE_SORT_STR, "%s%s" % (str(package_update.level), package_update.alias))
                        model.set_value(iter, UPDATE_OBJ, package_update)
                        num_visible = num_visible + 1

                Gdk.threads_enter()

                is_enable_tools = True
                if (new_gooroomupdate):
                    self.statusString = _("A new version of the update manager is available")
                    self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_updates))
                    self.statusIcon.set_tooltip_text(self.statusString)
                    self.statusIcon.set_visible(True)
                    statusbar.push(context_id, self.statusString)
                    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Found a new version of gooroom-update\n")
                    log.flush()
                else:
                    try:
                        pp = Popen(
                            ['/usr/bin/apt-cache', 'policy'],
                            stdout=PIPE,
                            stderr=PIPE)
                        pp_out, pp_err = pp.communicate()
                        
                        package_server_url = ''
                        for lo in pp_out.split('\n'):
                            splited_lo = lo.split()
                            if len(splited_lo) > 1 \
                                and (splited_lo[1].startswith('http://') \
                                or splited_lo[1].startswith('https://')):

                                r_splited_lo = splited_lo[1].split('/')
                                if r_splited_lo[-1] == 'debian':
                                    package_server_url = '/'.join(r_splited_lo[:-1])
                                # 데비안 업데이트 서버 주소가 명시되지 않은 경우
                                # 구름 업데이트 서버 주소를 이용하여 연결 유무 확인
                                elif r_splited_lo[-1] == 'gooroom':
                                    package_server_url = '/'.join(r_splited_lo[:-1])

                        if package_server_url == '':
                            net_status = _("Failed")
                        else:
                            urllib2.urlopen(package_server_url, timeout=2)
                            print "network connection ok"
                            net_status = _("OK")

                    except urllib2.URLError as err:
                        if err.code == 200 or err.code == 403:
                            print "network connection ok"
                            net_status = _("OK")
                        else:
                            print "network connection failed"
                            net_status = _("Failed")

                    if (num_safe > 0):
                        x, y = wTree.get_object("window").get_position()

                        ## 1. The alert pop-up is new
                        if alert == True:
                            # pop-up for updater
                            wTree.get_object("window").move(x, y)
                            wTree.get_object("window").present()
                            wTree.get_object("window").show_all()

                            image = Gtk.Image()
                            image.set_from_stock(Gtk.STOCK_DIALOG_INFO, Gtk.IconSize.DIALOG)
                            image.show()
                            alertDialog = Gtk.MessageDialog(None, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.INFO, Gtk.ButtonsType.OK, None)
                            alertDialog.set_title(_("warning updater"))
                            alertDialog.set_image(image)

                            # set font size
                            message = "<span font_weight='bold' size='large'>%s</span>" % _("Found a new version in updater.\nPlease run the update.")
                            alertDialog.set_markup(message)

                            # set the center position
                            #wTree.get_object("window").set_position(Gtk.WindowPosition.CENTER_ALWAYS)
                            alertDialog.set_position(Gtk.WindowPosition.CENTER_ALWAYS)

                            alert = False

                            response = alertDialog.run()
                            if response == Gtk.ResponseType.OK or response == Gtk.ResponseType.DELETE_EVENT:
                                alertDialog.hide()
                                alertDialog.destroy()
                                alert = True

                        ## 2. The alert pop-up already.
                        else:
                            xa, ya = alertDialog.get_position()
                            alertDialog.move(xa, ya)
                            alertDialog.present()
                            alertDialog.show()

                        # pop-up for updater
                        wTree.get_object("window").move(x, y)
                        wTree.get_object("window").present()
                        wTree.get_object("window").show_all()

                        if (num_safe == 1):
                            if (num_ignored == 0):
                                self.statusString = _("1 recommended update available (%(size)s)") % {'size':size_to_string(download_size)}
                                self.statusString += " : " + _("Network connection is %s") % net_status
                            elif (num_ignored == 1):
                                self.statusString = _("1 recommended update available (%(size)s), 1 ignored") % {'size':size_to_string(download_size)}
                                self.statusString += " : " + _("Network connection is %s") % net_status
                            elif (num_ignored > 1):
                                self.statusString = _("1 recommended update available (%(size)s), %(ignored)d ignored") % {'size':size_to_string(download_size), 'ignored':num_ignored}
                                self.statusString += " : " + _("Network connection is %s") % net_status
                        else:
                            if (num_ignored == 0):
                                self.statusString = _("%(recommended)d recommended updates available (%(size)s)") % {'recommended':num_safe, 'size':size_to_string(download_size)}
                                self.statusString += " : " + _("Network connection is %s") % net_status
                            elif (num_ignored == 1):
                                self.statusString = _("%(recommended)d recommended updates available (%(size)s), 1 ignored") % {'recommended':num_safe, 'size':size_to_string(download_size)}
                                self.statusString += " : " + _("Network connection is %s") % net_status
                            elif (num_ignored > 0):
                                self.statusString = _("%(recommended)d recommended updates available (%(size)s), %(ignored)d ignored") % {'recommended':num_safe, 'size':size_to_string(download_size), 'ignored':num_ignored}
                                self.statusString += " : " + _("Network connection is %s") % net_status
                        self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_updates))
                        self.statusIcon.set_tooltip_text(self.statusString)
                        self.statusIcon.set_visible(True)
                        statusbar.push(context_id, self.statusString)
                        log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Found " + str(num_safe) + " recommended software updates\n")
                        log.flush()
                    else:
                        if num_visible == 0:
                            self.wTree.get_object("notebook_status").set_current_page(TAB_UPTODATE)
                        is_enable_tools = False
                        self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_up2date))
                        self.statusString = _("Your system is up to date")
                        self.statusString += " : " + _("Network connection is %s") % net_status
                        self.statusIcon.set_tooltip_text(self.statusString)
                        statusbar.push(context_id,self.statusString)
                        log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ System is up to date\n")
                        log.flush()

                Gdk.threads_leave()

            Gdk.threads_enter()
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Refresh finished\n")
            log.flush()
            # Stop the blinking
            #self.statusIcon.set_blinking(False)
            self.wTree.get_object("tool_select_all").set_sensitive(is_enable_tools)
            self.wTree.get_object("tool_clear").set_sensitive(is_enable_tools)
            self.wTree.get_object("tool_apply").set_sensitive(is_enable_tools)
            self.wTree.get_object("notebook_details").set_current_page(0)
            self.wTree.get_object("window").get_window().set_cursor(None)
            self.treeview_update.set_model(model)
            del model
            self.wTree.get_object("window").set_sensitive(True)
            wTree.get_object("vpaned1").set_position(vpaned_position)
            Gdk.threads_leave()
        except Exception, detail:
            Gdk.threads_leave()
            log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Exception occurred in the refresh thread: " + str(detail) + "\n")
            log.flush()

            Gdk.threads_enter()
            self.statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_error))
            self.statusIcon.set_tooltip_text(_("Could not refresh the list of updates"))
            self.statusIcon.set_visible(True)
            #self.statusIcon.set_blinking(False)
            self.wTree.get_object("window").get_window().set_cursor(None)
            self.wTree.get_object("window").set_sensitive(True)
            statusbar.push(context_id, _("Could not refresh the list of updates"))
            wTree.get_object("vpaned1").set_position(vpaned_position)
            Gdk.threads_leave()

    def checkDependencies(self, changes, cache):
        foundSomething = False
        for pkg in changes:
            for dep in pkg.candidateDependencies:
                for o in dep.or_dependencies:
                    try:
                        if cache[o.name].isUpgradable:
                            pkgFound = False
                            for pkg2 in changes:
                                if o.name == pkg2.name:
                                    pkgFound = True
                            if pkgFound == False:
                                newPkg = cache[o.name]
                                changes.append(newPkg)
                                foundSomething = True
                    except Exception, detail:
                        pass # don't know why we get these..
        if (foundSomething):
            changes = self.checkDependencies(changes, cache)
        return changes

def force_refresh(widget, treeview, statusIcon, wTree):
    refresh = RefreshThread(treeview, statusIcon, wTree, root_mode=True)
    refresh.start()

def clear(widget, treeView, statusbar, apply_button, context_id):
    model = treeView.get_model()
    iter = model.get_iter_first()
    while (iter != None):
        model.set_value(iter, 0, "false")
        iter = model.iter_next(iter)
    statusbar.push(context_id, _("No updates selected"))
    apply_button.set_sensitive(False)

def select_all(widget, treeView, statusbar, apply_button, context_id):
    model = treeView.get_model()
    iter = model.get_iter_first()
    while (iter != None):
        model.set_value(iter, UPDATE_CHECKED, "true")
        iter = model.iter_next(iter)
    iter = model.get_iter_first()
    download_size = 0
    num_selected = 0
    while (iter != None):
        checked = model.get_value(iter, UPDATE_CHECKED)
        if (checked == "true"):
            size = model.get_value(iter, UPDATE_SIZE)
            download_size = download_size + size
            num_selected = num_selected + 1
        iter = model.iter_next(iter)
    if num_selected == 0:
        statusbar.push(context_id, _("No updates selected"))
    elif num_selected == 1:
        statusbar.push(context_id, _("%(selected)d update selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})
    else:
        statusbar.push(context_id, _("%(selected)d updates selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})

    apply_button.set_sensitive(True)

def install(widget, treeView, statusIcon, wTree):
    install = InstallThread(treeView, statusIcon, wTree)
    install.start()

def pixbuf_trayicon(filename):
    size = 22
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(filename, size, size)
    return pixbuf

def pref_apply(widget, prefs_tree, treeview, statusIcon, wTree):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    config = ConfigObj("%s/gooroomUpdate.conf" % CONFIG_DIR)

    #Write level config
    config['levels'] = {}
    config['levels']['level1_visible'] = prefs_tree.get_object("visible1").get_active()
    config['levels']['level2_visible'] = prefs_tree.get_object("visible2").get_active()
    config['levels']['level3_visible'] = prefs_tree.get_object("visible3").get_active()
    #config['levels']['level4_visible'] = prefs_tree.get_object("visible4").get_active()
    #config['levels']['level5_visible'] = prefs_tree.get_object("visible5").get_active()
    config['levels']['level1_safe'] = prefs_tree.get_object("safe1").get_active()
    config['levels']['level2_safe'] = prefs_tree.get_object("safe2").get_active()
    config['levels']['level3_safe'] = prefs_tree.get_object("safe3").get_active()
    #config['levels']['level4_safe'] = prefs_tree.get_object("safe4").get_active()
    #config['levels']['level5_safe'] = prefs_tree.get_object("safe5").get_active()
    config['levels']['security_visible'] = prefs_tree.get_object("checkbutton_security_visible").get_active()
    config['levels']['security_safe'] = prefs_tree.get_object("checkbutton_security_safe").get_active()

    #Write refresh config
    config['refresh'] = {}
    config['refresh']['timer_minutes'] = int(prefs_tree.get_object("timer_minutes").get_value())
    config['refresh']['timer_hours'] = int(prefs_tree.get_object("timer_hours").get_value())
    config['refresh']['timer_days'] = int(prefs_tree.get_object("timer_days").get_value())

    auto_upgrade = prefs_tree.get_object("auto_upgrade").get_active()
    if (auto_upgrade != get_auto_upgrade()):
        auto_upgrade_script = "pkexec /usr/sbin/auto-upgrade-script.sh {}".format(\
                             ('true' if auto_upgrade else 'false'))
        os.system(auto_upgrade_script)

    config['auto_upgrade']= {}
    config['auto_upgrade']['date']= int(prefs_tree.get_object("auto_upgrade_date").get_active())
    config['auto_upgrade']['time']= int(prefs_tree.get_object("auto_upgrade_time").get_active())


    #Write update config
    config['update'] = {}
    config['update']['dist_upgrade'] = prefs_tree.get_object("checkbutton_dist_upgrade").get_active()

    #Write icons config
    config['icons'] = {}
    config['icons']['busy'] = icon_busy
    config['icons']['up2date'] = icon_up2date
    config['icons']['updates'] = icon_updates
    config['icons']['error'] = icon_error
    config['icons']['unknown'] = icon_unknown
    config['icons']['apply'] = icon_apply

    config.write()

    prefs_tree.get_object("window").hide()
    refresh = RefreshThread(treeview, statusIcon, wTree)
    refresh.start()

    #gooroom
    global app_hidden
    app_hidden = True

    global is_pref_opened
    is_pref_opened = False


def info_cancel(widget, prefs_tree):
    prefs_tree.get_object("window").hide()

def history_cancel(widget, tree):
    tree.get_object("window").hide()

def pref_destroy(widget, prefs_tree):
    global is_pref_opened
    is_pref_opened = False

    prefs_tree.get_object("window").hide()

def pref_cancel(widget, prefs_tree):
    global is_pref_opened
    is_pref_opened = False

    prefs_tree.get_object("window").hide()

def set_auto_upgrade(widget, prefs_tree):
    #FIXME this method has probability that changes other sudoers.d/gooroom-update configuration.
    global auto_upgrade_handler_id
    toggle = prefs_tree.get_object("auto_upgrade").get_active()

    prefs_tree.get_object("auto_upgrade_time").set_sensitive(toggle)
    prefs_tree.get_object("auto_upgrade_date").set_sensitive(toggle)

def get_auto_upgrade():
    with open("/etc/gooroom/gooroom-update/auto-upgrade", "r") as f:
        auto_install_flag = f.read().strip("\n")
    if "1" == auto_install_flag:
        return True
    return False

def read_configuration():
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    config = ConfigObj("%s/gooroomUpdate.conf" % CONFIG_DIR)
    prefs = {}

    #Read refresh config
    try:
        prefs["timer_minutes"] = int(config['refresh']['timer_minutes'])
        prefs["timer_hours"] = int(config['refresh']['timer_hours'])
        prefs["timer_days"] = int(config['refresh']['timer_days'])
    except:
        prefs["timer_minutes"] = 30
        prefs["timer_hours"] = 0
        prefs["timer_days"] = 0

    try:
        prefs["auto_upgrade"]= get_auto_upgrade()
        prefs["auto_upgrade_date"]= int(config['auto_upgrade']['date'])
        prefs["auto_upgrade_time"] = int(config['auto_upgrade']['time'])
    except:
        prefs["auto_upgrade"]= False
        prefs["auto_upgrade_date"]=0
        prefs["auto_upgrade_time"]=0

    #Read update config
    try:
        prefs["dist_upgrade"] = (config['update']['dist_upgrade'] == "True")
    except:
        prefs["dist_upgrade"] = True

    #Read icons config
    try:
        icon_busy = config['icons']['busy']
        icon_up2date = config['icons']['up2date']
        icon_updates = config['icons']['updates']
        icon_error = config['icons']['error']
        icon_unknown = config['icons']['unknown']
        icon_apply = config['icons']['apply']
    except:
        icon_busy = "/usr/lib/gooroom/gooroomUpdate/icons/base.svg"
        icon_up2date = "/usr/lib/gooroom/gooroomUpdate/icons/base-apply.svg"
        icon_updates = "/usr/lib/gooroom/gooroomUpdate/icons/base-info.svg"
        icon_error = "/usr/lib/gooroom/gooroomUpdate/icons/base-error2.svg"
        icon_unknown = "/usr/lib/gooroom/gooroomUpdate/icons/base-unkown.svg"
        icon_apply = "/usr/lib/gooroom/gooroomUpdate/icons/base-exec.svg"

    #Read levels config
    try:
        prefs["level1_visible"] = (config['levels']['level1_visible'] == "True")
        prefs["level2_visible"] = (config['levels']['level2_visible'] == "True")
        prefs["level3_visible"] = (config['levels']['level3_visible'] == "True")
        #prefs["level4_visible"] = (config['levels']['level4_visible'] == "True")
        #prefs["level5_visible"] = (config['levels']['level5_visible'] == "True")
        prefs["level1_safe"] = (config['levels']['level1_safe'] == "True")
        prefs["level2_safe"] = (config['levels']['level2_safe'] == "True")
        prefs["level3_safe"] = (config['levels']['level3_safe'] == "True")
        #prefs["level4_safe"] = (config['levels']['level4_safe'] == "True")
        #prefs["level5_safe"] = (config['levels']['level5_safe'] == "True")
        prefs["security_visible"] = (config['levels']['security_visible'] == "True")
        prefs["security_safe"] = (config['levels']['security_safe'] == "True")
    except:
        prefs["level1_visible"] = True
        prefs["level2_visible"] = True
        prefs["level3_visible"] = True
        #prefs["level4_visible"] = False
        #prefs["level5_visible"] = False
        prefs["level1_safe"] = True
        prefs["level2_safe"] = True
        prefs["level3_safe"] = True
        #prefs["level4_safe"] = False
        #prefs["level5_safe"] = False
        prefs["security_visible"] = False
        prefs["security_safe"] = False

    #Read columns config
    try:
        prefs["type_column_visible"] = (config['visible_columns']['type'] == "True")
    except:
        prefs["type_column_visible"] = True
    try:
        prefs["level_column_visible"] = (config['visible_columns']['level'] == "True")
    except:
        prefs["level_column_visible"] = True
    try:
        prefs["package_column_visible"] = (config['visible_columns']['package'] == "True")
    except:
        prefs["package_column_visible"] = True
    try:
        prefs["old_version_column_visible"] = (config['visible_columns']['old_version'] == "True")
    except:
        prefs["old_version_column_visible"] = False
    try:
        prefs["new_version_column_visible"] = (config['visible_columns']['new_version'] == "True")
    except:
        prefs["new_version_column_visible"] = True
    try:
        prefs["size_column_visible"] = (config['visible_columns']['size'] == "True")
    except:
        prefs["size_column_visible"] = False
    try:
        prefs["descriptions_visible"] = (config['visible_columns']['description'] == "True")
    except:
        prefs["descriptions_visible"] = True

    #Read window dimensions
    try:
        prefs["dimensions_x"] = int(config['dimensions']['x'])
        prefs["dimensions_y"] = int(config['dimensions']['y'])
        prefs["dimensions_pane_position"] = int(config['dimensions']['pane_position'])
    except:
        prefs["dimensions_x"] = 790
        prefs["dimensions_y"] = 540
        # jeong89
        #prefs["dimensions_pane_position"] = 278
        prefs["dimensions_pane_position"] = 3

    return prefs

def open_synaptic_package_manager(widget):
    file_stat = os.stat("/usr/bin/synaptic-pkexec")
    if file_stat.st_mode & stat.S_IXOTH:
        os.system("/usr/bin/synaptic-pkexec")
    else:
        permissionAlertDialog = Gtk.MessageDialog(None, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.INFO, Gtk.ButtonsType.OK, None)
        permissionAlertDialog.set_title(_("Error Launching Program"))

        message = "<span font_weight='bold' size='large'>%s</span>" % _("Can't execute Synpatic Package Manager.\nThis software is prohibited to run.")
        permissionAlertDialog.set_markup(message)
        permissionAlertDialog.set_position(Gtk.WindowPosition.CENTER_ALWAYS)

        response = permissionAlertDialog.run()
        if response == Gtk.ResponseType.OK or response == Gtk.ResponseType.DELETE_EVENT:
            permissionAlertDialog.hide()
            permissionAlertDialog.destroy()

def open_repositories(widget):
    if os.path.exists("/usr/bin/software-sources"):
        os.system("/usr/bin/software-sources &")
    elif os.path.exists("/usr/bin/software-properties-gtk"):
        os.system("/usr/bin/software-properties-gtk &")
    elif os.path.exists("/usr/bin/software-properties-kde"):
        os.system("/usr/bin/software-properties-kde &")

def open_preferences(widget, treeview, statusIcon, wTree):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply
    global auto_upgrade_handler_id
    global is_pref_opened

    if is_pref_opened:
        return

    is_pref_opened = True

    #gladefile = "/usr/lib/gooroom/gooroomUpdate/gooroomUpdate.glade"
    gladefile = "/usr/lib/gooroom/gooroomUpdate/preference.glade"
    #prefs_tree = gtk.glade.XML(gladefile, "window2")
    prefs_tree = Gtk.Builder().new_from_file(gladefile)
    prefs_tree.get_object("window").set_title(_("Preferences") + " - " + _("Update Manager"))

    prefs_tree.get_object("label37").set_text(_("Levels"))
    prefs_tree.get_object("label36").set_text(_("Auto-Refresh & Upgrade"))
    prefs_tree.get_object("label39").set_markup("<b>" + _("Level") + "</b>")
    prefs_tree.get_object("label40").set_markup("<b>" + _("Description") + "</b>")
    prefs_tree.get_object("label48").set_markup("<b>" + _("Tested?") + "</b>")
    prefs_tree.get_object("label54").set_markup("<b>" + _("Origin") + "</b>")
    prefs_tree.get_object("label41").set_markup("<b>" + _("Safe?") + "</b>")
    prefs_tree.get_object("label42").set_markup("<b>" + _("Visible?") + "</b>")
    prefs_tree.get_object("label43").set_text(_("Packages provided by Gooroom"))
    prefs_tree.get_object("label44").set_text(_("Packages provided by Debian"))
    prefs_tree.get_object("label45").set_text(_("Packages provided by 3rd-party"))
    #prefs_tree.get_object("label46").set_text(_("Unsafe updates. Could potentially affect the stability of the system."))
    #prefs_tree.get_object("label47").set_text(_("Dangerous updates. Known to affect the stability of the systems depending on certain specs or hardware."))
    prefs_tree.get_object("label55").set_text(_("Gooroom"))
    prefs_tree.get_object("label56").set_text(_("Debian"))
    prefs_tree.get_object("label57").set_text(_("3rd-party"))
    #prefs_tree.get_object("label58").set_text(_("Upstream"))
    #prefs_tree.get_object("label59").set_text(_("Upstream"))
    prefs_tree.get_object("label81").set_text(_("Refresh the list of updates every:"))
    prefs_tree.get_object("label82").set_text("<i>" + _("Note: The list only gets refreshed while the update manager window is closed (system tray mode).") + "</i>")
    prefs_tree.get_object("label82").set_use_markup(True)
    prefs_tree.get_object("label83").set_text(_("Options"))

    prefs_tree.get_object("auto_upgrade").set_label(_("Auto Upgrade"))
    prefs_tree.get_object("label4").set_text(_("New Upgrade"))
    prefs_tree.get_object("label5").set_text(_("Time"))
    prefs_tree.get_object("label6").set_text(_("The system will be upgraded automatically with the latest software if it is on and running at the time."))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Saturday"))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Friday"))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Thursday"))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Wednesday"))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Tuesday"))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Monday"))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Sunday"))
    prefs_tree.get_object("auto_upgrade_date").insert_text(0, _("Every Day"))

    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 12:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 1:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 2:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 3:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 4:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 5:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 6:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 7:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 8:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 9:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 10:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("AM 11:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 12:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 1:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 2:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 3:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 4:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 5:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 6:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 7:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 8:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 9:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 10:00"))
    prefs_tree.get_object("auto_upgrade_time").insert_text(0, _("PM 11:00"))

    prefs_tree.get_object("checkbutton_dist_upgrade").set_label(_("Include updates which require the installation of new packages or the removal of installed packages"))

    prefs_tree.get_object("window").set_icon_name("gooroomupdater")
    prefs_tree.get_object("window").set_keep_above(True)
    prefs_tree.get_object("window").connect("destroy", pref_destroy, prefs_tree)
    prefs_tree.get_object("window").show()
    prefs_tree.get_object("pref_button_cancel").connect("clicked", pref_cancel, prefs_tree)
    prefs_tree.get_object("pref_button_apply").connect("clicked", pref_apply, prefs_tree, treeview, statusIcon, wTree)

    prefs = read_configuration()

    prefs_tree.get_object("visible1").set_active(prefs["level1_visible"])
    prefs_tree.get_object("visible2").set_active(prefs["level2_visible"])
    prefs_tree.get_object("visible3").set_active(prefs["level3_visible"])
    #prefs_tree.get_object("visible4").set_active(prefs["level4_visible"])
    #prefs_tree.get_object("visible5").set_active(prefs["level5_visible"])
    prefs_tree.get_object("safe1").set_active(prefs["level1_safe"])
    prefs_tree.get_object("safe2").set_active(prefs["level2_safe"])
    prefs_tree.get_object("safe3").set_active(prefs["level3_safe"])
    #prefs_tree.get_object("safe4").set_active(prefs["level4_safe"])
    #prefs_tree.get_object("safe5").set_active(prefs["level5_safe"])
    prefs_tree.get_object("checkbutton_security_visible").set_active(prefs["security_visible"])
    prefs_tree.get_object("checkbutton_security_safe").set_active(prefs["security_safe"])

    prefs_tree.get_object("checkbutton_security_visible").set_label(_("Always show security updates"))
    prefs_tree.get_object("checkbutton_security_safe").set_label(_("Always select and trust security updates"))

    prefs_tree.get_object("timer_minutes_label").set_text(_("minutes"))
    prefs_tree.get_object("timer_hours_label").set_text(_("hours"))
    prefs_tree.get_object("timer_days_label").set_text(_("days"))
    prefs_tree.get_object("timer_minutes").set_value(prefs["timer_minutes"])
    prefs_tree.get_object("timer_hours").set_value(prefs["timer_hours"])
    prefs_tree.get_object("timer_days").set_value(prefs["timer_days"])

    prefs_tree.get_object("auto_upgrade").set_active(prefs["auto_upgrade"])
    prefs_tree.get_object("auto_upgrade_date").set_active(prefs["auto_upgrade_date"])
    prefs_tree.get_object("auto_upgrade_time").set_active(prefs["auto_upgrade_time"])
    prefs_tree.get_object("auto_upgrade").connect("toggled", set_auto_upgrade, prefs_tree)

    if prefs_tree.get_object("auto_upgrade").get_active()== False:
        prefs_tree.get_object("auto_upgrade_date").set_sensitive(False)
        prefs_tree.get_object("auto_upgrade_time").set_sensitive(False)
    else:
        prefs_tree.get_object("auto_upgrade_date").set_sensitive(True)
        prefs_tree.get_object("auto_upgrade_time").set_sensitive(True)

    prefs_tree.get_object("checkbutton_dist_upgrade").set_active(prefs["dist_upgrade"])

def open_history(widget):
    #Set the Glade file
    #gladefile = "/usr/lib/gooroom/gooroomUpdate/gooroomUpdate.glade"
    gladefile = "/usr/lib/gooroom/gooroomUpdate/history.glade"
    #wTree = gtk.glade.XML(gladefile, "window4")
    wTree = Gtk.Builder().new_from_file(gladefile)
    treeview_update = wTree.get_object("treeview_history")
    wTree.get_object("window").set_icon_name("gooroomupdater")
    wTree.get_object("window").set_title(_("History of updates") + " - " + _("Update Manager"))

    # the treeview
    column1 = Gtk.TreeViewColumn(_("Date"), Gtk.CellRendererText(), text=1)
    column1.set_sort_column_id(1)
    column1.set_resizable(True)
    column2 = Gtk.TreeViewColumn(_("Package"), Gtk.CellRendererText(), text=0)
    column2.set_sort_column_id(0)
    column2.set_resizable(True)
    column3 = Gtk.TreeViewColumn(_("Old version"), Gtk.CellRendererText(), text=2)
    column3.set_sort_column_id(2)
    column3.set_resizable(True)
    column4 = Gtk.TreeViewColumn(_("New version"), Gtk.CellRendererText(), text=3)
    column4.set_sort_column_id(3)
    column4.set_resizable(True)

    treeview_update.append_column(column1)
    treeview_update.append_column(column2)
    treeview_update.append_column(column3)
    treeview_update.append_column(column4)

    treeview_update.set_headers_clickable(True)
    treeview_update.set_reorderable(False)
    treeview_update.set_search_column(0)
    treeview_update.set_enable_search(True)
    treeview_update.show()

    model = Gtk.TreeStore(str, str, str, str) # (packageName, date, oldVersion, newVersion)

    if (os.path.exists("/var/log/dpkg.log")):
        updates = commands.getoutput("cat /var/log/dpkg.log /var/log/dpkg.log.? 2>/dev/null | egrep \"upgrade\"")
        updates = string.split(updates, "\n")
        for pkg in updates:
            values = string.split(pkg, " ")
            if len(values) == 6:
                date = values[0]
                time = values[1]
                action = values[2]
                package = values[3]
                oldVersion = values[4]
                newVersion = values[5]

                if action != "upgrade":
                    continue

                if oldVersion == newVersion:
                    continue

                if ":" in package:
                    package = package.split(":")[0]

                iter = model.insert_before(None, None)
                model.set_value(iter, 0, package)
                model.row_changed(model.get_path(iter), iter)
                model.set_value(iter, 1, "%s - %s" % (date, time))
                model.set_value(iter, 2, oldVersion)
                model.set_value(iter, 3, newVersion)

    model.set_sort_column_id( 1, Gtk.SortType.DESCENDING )
    treeview_update.set_model(model)
    del model
    wTree.get_object("button_close").connect("clicked", history_cancel, wTree)

def open_information(widget):
    global logFile
    global pid

    #gladefile = "/usr/lib/gooroom/gooroomUpdate/gooroomUpdate.glade"
    gladefile = "/usr/lib/gooroom/gooroomUpdate/information.glade"
    prefs_tree = Gtk.Builder().new_from_file(gladefile)
    prefs_tree.get_object("window").set_title(_("Information") + " - " + _("Update Manager"))
    prefs_tree.get_object("window").set_icon_name("gooroomupdater")
    prefs_tree.get_object("close_button3").connect("clicked", info_cancel, prefs_tree)
    prefs_tree.get_object("label4").set_text(_("Process ID:"))
    prefs_tree.get_object("label5").set_text(_("Log file:"))
    prefs_tree.get_object("processid_label").set_text(str(pid))
    prefs_tree.get_object("log_filename").set_text(str(logFile))
    txtbuffer = Gtk.TextBuffer()
    txtbuffer.set_text(commands.getoutput("cat " + logFile))
    prefs_tree.get_object("log_textview").set_buffer(txtbuffer)

def label_size_allocate(widget, rect):
    widget.set_size_request(rect.width, -1)

def open_help(widget):
    os.system("yelp help:gooroom/software-updates &")

def open_about(widget):
    dlg = Gtk.AboutDialog()
    dlg.set_title(_("About") + " - " + _("Update Manager"))
    dlg.set_program_name("gooroomupdater")
    dlg.set_comments(_("Update Manager"))
    try:
        h = open('/usr/share/common-licenses/GPL','r')
        s = h.readlines()
        gpl = ""
        for line in s:
            gpl += line
        h.close()
        dlg.set_license(gpl)
    except Exception, detail:
        print detail
    try:
        cache = apt.Cache()
        pkg = cache["gooroom-update"]
        if pkg.installed is not None:
            version = pkg.installed.version
        else:
            version = ""
        dlg.set_version(version)
    except Exception, detail:
        print detail

    dlg.set_authors(["Clement Lefebvre <root@linuxmint.com>", "Chris Hodapp <clhodapp@live.com>","Gooroom <gooroom@gooroom.kr>"])
    dlg.set_icon_name("gooroomupdater")
    dlg.set_logo(GdkPixbuf.Pixbuf.new_from_file("/usr/lib/gooroom/gooroomUpdate/icons/base.svg"))
    def close(w, res):
        if res == Gtk.ResponseType.CANCEL:
            w.hide()
    dlg.connect("response", close)
    dlg.show()

def quit_cb(widget, window, vpaned, data = None):
    global log
    if data:
        data.set_visible(False)
    try:
        log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Exiting - requested by user\n")
        log.flush()
        log.close()
        save_window_size(window, vpaned)
    except:
        pass # cause log might already been closed
    # Whatever works best heh :)
    pid = os.getpid()
    os.system("kill -9 %s &" % pid)
    #gtk.main_quit()
    #sys.exit(0)

def popup_menu_cb(widget, button, time, data = None):
    if button == 3:
        if data:
            data.show_all()
            #data.popup(None, None, Gtk.StatusIcon.position_menu, 3, time, widget)
            data.popup(None, None, Gtk.StatusIcon.position_menu, widget, 3, time)
    pass

def close_window(window, event, vpaned):
    global app_hidden
    window.hide()
    save_window_size(window, vpaned)
    app_hidden = True
    return True

def hide_window(widget, window):
    global app_hidden
    window.hide()
    app_hidden = True

def activate_icon_cb(widget, data, wTree):
    global app_hidden
    if (app_hidden == True):
        wTree.get_object("window").show_all()
        app_hidden = False
    else:
        wTree.get_object("window").hide()
        app_hidden = True
        save_window_size(wTree.get_object("window"), wTree.get_object("vpaned1"))

def save_window_size(window, vpaned):

    config = ConfigObj("%s/gooroomUpdate.conf" % CONFIG_DIR)
    config['dimensions'] = {}
    config['dimensions']['x'] = window.get_size()[0]
    config['dimensions']['y'] = window.get_size()[1]
    config['dimensions']['pane_position'] = vpaned.get_position()
    config.write()

def clean_l10n_short_description(description):
        try:
            # Remove "Description-xx: " prefix
            value = re.sub(r'Description-(\S+): ', r'', description)
            # Only take the first line and trim it
            value = value.split("\n")[0].strip()
            # Capitalize the first letter
            value = value[:1].upper() + value[1:]
            # Add missing punctuation
            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                value = "%s." % value
            # Replace & signs with &amp; (because we pango it)
            value = value.replace('&', '&amp;')

            return value
        except Exception, detail:
            print detail
            return description

def clean_l10n_description(description):
        try:
            lines = description.split("\n")
            value = ""
            num = 0
            newline = False
            for line in lines:
                line = line.strip()
                if len(line) > 0:
                    if line == ".":
                        value = "%s\n" % (value)
                        newline = True
                    else:
                        if (newline):
                            value = "%s%s" % (value, line.capitalize())
                        else:
                            value = "%s %s" % (value, line)
                        newline = False
                    num += 1
            value = value.replace("  ", " ").strip()
            # Capitalize the first letter
            value = value[:1].upper() + value[1:]
            # Add missing punctuation
            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                value = "%s." % value
            return value
        except Exception, detail:
            print detail
            return description

def l10n_descriptions(package_update):
        package_name = package_update.name.replace(":i386", "").replace(":amd64", "")
        if package_descriptions.has_key(package_name):
            package_update.short_description = package_short_descriptions[package_name]
            package_update.description = package_descriptions[package_name]

def display_selected_package(selection, wTree):
    try:
        wTree.get_object("textview_description").get_buffer().set_text("")
        wTree.get_object("textview_changes").get_buffer().set_text("")
        (model, iter) = selection.get_selected()
        if (iter != None):
            package_update = model.get_value(iter, UPDATE_OBJ)
            if wTree.get_object("notebook_details").get_current_page() == 0:
                # Description tab
                description = package_update.description
                buffer = wTree.get_object("textview_description").get_buffer()
                buffer.set_text(description)
                from gi.repository import Pango
                try:
                    buffer.create_tag("dimmed", scale=Pango.SCALE, foreground="#5C5C5C", style=Pango.Style.ITALIC)
                except:
                    # Already exists, no big deal..
                    pass
                if (len(package_update.packages) > 1):
                    dimmed_description = "\n%s %s" % (_("This update contains %d packages: ") % len(package_update.packages), " ".join(package_update.packages))
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
                elif (package_update.packages[0] != package_update.alias):
                    dimmed_description = "\n%s %s" % (_("This update contains 1 package: "), package_update.packages[0])
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
            else:
                # Changelog tab
                retriever = ChangelogRetriever(package_update, wTree)
                retriever.start()

    except Exception, detail:
        print detail

def switch_page(notebook, page, page_num, Wtree, treeView):
    selection = treeView.get_selection()
    (model, iter) = selection.get_selected()
    if (iter != None):
        package_update = model.get_value(iter, UPDATE_OBJ)
        if (page_num == 0):
            # Description tab
            description = package_update.description
            buffer = wTree.get_object("textview_description").get_buffer()
            buffer.set_text(description)
            from gi.repository import Pango
            try:
                buffer.create_tag("dimmed", scale=Pango.SCALE, foreground="#5C5C5C", style=Pango.Style.ITALIC)
            except:
                # Already exists, no big deal..
                pass
            if (len(package_update.packages) > 1):
                dimmed_description = "\n%s %s" % (_("This update contains %d packages: ") % len(package_update.packages), " ".join(package_update.packages))
                buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
            elif (package_update.packages[0] != package_update.name):
                dimmed_description = "\n%s %s" % (_("This update contains 1 package: "), package_update.packages[0])
                buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
        else:
            # Changelog tab
            retriever = ChangelogRetriever(package_update, wTree)
            retriever.start()

def celldatafunction_checkbox(column, cell, model, iter, *data):
    cell.set_property("activatable", True)
    checked = model.get_value(iter, UPDATE_CHECKED)
    if (checked == "true"):
        cell.set_property("active", True)
    else:
        cell.set_property("active", False)

def toggled(renderer, path, treeview, statusbar, apply_button, context_id):
    model = treeview.get_model()
    iter = model.get_iter(path)

    if (iter != None):
        checked = model.get_value(iter, UPDATE_CHECKED)
        if (checked == "true"):
            model.set_value(iter, UPDATE_CHECKED, "false")
        else:
            model.set_value(iter, UPDATE_CHECKED, "true")
        

    iter = model.get_iter_first()
    download_size = 0
    num_selected = 0
    is_enable_apply = True
    while (iter != None):
        checked = model.get_value(iter, UPDATE_CHECKED)
        if (checked == "true"):
            size = model.get_value(iter, UPDATE_SIZE)
            download_size = download_size + size
            num_selected = num_selected + 1
        iter = model.iter_next(iter)
    if num_selected == 0:
        statusbar.push(context_id, _("No updates selected"))
        is_enable_apply = False
    elif num_selected == 1:
        statusbar.push(context_id, _("%(selected)d update selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})
    else:
        statusbar.push(context_id, _("%(selected)d updates selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})

    apply_button.set_sensitive(is_enable_apply)

def size_to_string(size):
    strSize = str(size) + _("B")
    if (size >= 1024):
        strSize = str(size / 1024) + _("KB")
    if (size >= (1024 * 1024)):
        strSize = str(size / (1024 * 1024)) + _("MB")
    if (size >= (1024 * 1024 * 1024)):
        strSize = str(size / (1024 * 1024 * 1024)) + _("GB")
    return strSize

def setVisibleColumn(checkmenuitem, column, configName):
    config = ConfigObj("%s/gooroomUpdate.conf" % CONFIG_DIR)
    if (config.has_key('visible_columns')):
        config['visible_columns'][configName] = checkmenuitem.get_active()
    else:
        config['visible_columns'] = {}
        config['visible_columns'][configName] = checkmenuitem.get_active()
    config.write()
    column.set_visible(checkmenuitem.get_active())

def setVisibleDescriptions(checkmenuitem, treeView, statusIcon, wTree, prefs):
    config = ConfigObj("%s/gooroomUpdate.conf" % CONFIG_DIR)
    if (not config.has_key('visible_columns')):
        config['visible_columns'] = {}
    config['visible_columns']['description'] = checkmenuitem.get_active()
    config.write()
    prefs["descriptions_visible"] = checkmenuitem.get_active()
    refresh = RefreshThread(treeView, statusIcon, wTree)
    refresh.start()

global app_hidden
global log
global logFile
global pid
global statusbar
global context_id

app_hidden = True
alert = True

Gdk.threads_init()
Gdk.set_program_class("gooroomupdate")

# prepare the log
pid = os.getpid()
logdir = "/tmp/gooroomUpdate/"

if not os.path.exists(logdir):
    os.system("mkdir -p " + logdir)
    os.system("chmod a+rwx " + logdir)

log = tempfile.NamedTemporaryFile(prefix = logdir, delete=False)
logFile = log.name
try:
    os.system("chmod a+rw %s" % log.name)
except Exception, detail:
    print detail

log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Launching gooroomUpdate \n")
log.flush()

if (not os.path.exists(CONFIG_DIR)):
    os.system("mkdir -p %s" % CONFIG_DIR)
    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "++ Creating %s directory\n" % CONFIG_DIR)
    log.flush()

try:
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    prefs = read_configuration()

    statusIcon = Gtk.StatusIcon()
    statusIcon.set_from_pixbuf(pixbuf_trayicon(icon_busy))
    statusIcon.set_tooltip_text(_("Checking for updates"))

    #Set the Glade file
    gladefile = "/usr/lib/gooroom/gooroomUpdate/gooroomUpdate.glade"
    #wTree = gtk.glade.XML(gladefile, "window1")
    wTree = Gtk.Builder()
    wTree.add_from_file(gladefile)
    wTree.get_object("window").set_title(_("Update Manager"))
    wTree.get_object("window").set_default_size(prefs['dimensions_x'], prefs['dimensions_y'])
    wTree.get_object("vpaned1").set_position(prefs['dimensions_pane_position'])

    statusbar = wTree.get_object("statusbar")
    context_id = statusbar.get_context_id("gooroomupdater")

    vbox = wTree.get_object("vbox_main")
    treeview_update = wTree.get_object("treeview_update")
    wTree.get_object("window").set_icon_name("gooroomupdater")

    accel_group = Gtk.AccelGroup()
    wTree.get_object("window").add_accel_group(accel_group)

    # Get the window socket (needed for synaptic later on)

    if os.getuid() != 0 :
        # If we're not in root mode do that (don't know why it's needed.. very weird)
        socket = Gtk.Socket()
        vbox.pack_start(socket, False, False, 0)
        socket.show()
        window_id = repr(socket.get_id())

    # the treeview
    cr = Gtk.CellRendererToggle()
    cr.connect("toggled", toggled, treeview_update, statusbar, wTree.get_object("tool_apply"), context_id)
    column1 = Gtk.TreeViewColumn(_("Upgrade"), cr)
    column1.set_cell_data_func(cr, celldatafunction_checkbox)
    column1.set_sort_column_id(UPDATE_CHECKED)
    column1.set_resizable(True)

    column2 = Gtk.TreeViewColumn(_("Package"), Gtk.CellRendererText(), markup=UPDATE_ALIAS)
    column2.set_sort_column_id(UPDATE_ALIAS)
    column2.set_resizable(True)

    column3 = Gtk.TreeViewColumn(_("Level"), Gtk.CellRendererPixbuf(), pixbuf=UPDATE_LEVEL_PIX)
    column3.set_sort_column_id(UPDATE_LEVEL_STR)
    column3.set_resizable(True)

    column4 = Gtk.TreeViewColumn(_("Old version"), Gtk.CellRendererText(), text=UPDATE_OLD_VERSION)
    column4.set_sort_column_id(UPDATE_OLD_VERSION)
    column4.set_resizable(True)

    column5 = Gtk.TreeViewColumn(_("New version"), Gtk.CellRendererText(), text=UPDATE_NEW_VERSION)
    column5.set_sort_column_id(UPDATE_NEW_VERSION)
    column5.set_resizable(True)

    column6 = Gtk.TreeViewColumn(_("Size"), Gtk.CellRendererText(), text=UPDATE_SIZE_STR)
    column6.set_sort_column_id(UPDATE_SIZE)
    column6.set_resizable(True)

    column7 = Gtk.TreeViewColumn(_("Type"), Gtk.CellRendererPixbuf(), pixbuf=UPDATE_TYPE_PIX)
    column7.set_sort_column_id(UPDATE_TYPE)
    column7.set_resizable(True)

    treeview_update.set_tooltip_column(UPDATE_TOOLTIP)

    treeview_update.append_column(column7)
    treeview_update.append_column(column3)
    treeview_update.append_column(column1)
    treeview_update.append_column(column2)
    treeview_update.append_column(column4)
    treeview_update.append_column(column5)
    treeview_update.append_column(column6)

    treeview_update.set_headers_clickable(True)
    treeview_update.set_reorderable(False)
    treeview_update.show()

    selection = treeview_update.get_selection()
    selection.connect("changed", display_selected_package, wTree)
    wTree.get_object("notebook_details").connect("switch-page", switch_page, wTree, treeview_update)
    wTree.get_object("window").connect("delete_event", close_window, wTree.get_object("vpaned1"))
    wTree.get_object("tool_apply").connect("clicked", install, treeview_update, statusIcon, wTree)
    wTree.get_object("tool_clear").connect("clicked", clear, treeview_update, statusbar, wTree.get_object("tool_apply"), context_id)
    wTree.get_object("tool_select_all").connect("clicked", select_all, treeview_update, statusbar, wTree.get_object("tool_apply"), context_id)
    wTree.get_object("tool_refresh").connect("clicked", force_refresh, treeview_update, statusIcon, wTree)

    aist=AutoInstallScheduleThread(treeview_update, statusIcon, wTree)
    aist.start()

    global is_pref_opened
    is_pref_opened = False

    menu = wTree.get_object("popupmenu")
    menuitem1 = wTree.get_object("refreshmenu")
    menuitem1.set_label(_("_Refresh"))
    menuitem1.connect('activate', force_refresh, treeview_update, statusIcon, wTree)

    menuitem2 = wTree.get_object("infomenu")
    menuitem2.set_label(_("Information"))
    menuitem2.connect('activate', open_information)

    menuitem3 = wTree.get_object("prefmenu")
    menuitem3.set_label(_("_Preferences"))
    menuitem3.connect('activate', open_preferences, treeview_update, statusIcon, wTree)

    menuitem4 = wTree.get_object("quitmenu")
    menuitem4.set_label(_("_Quit"))
    menuitem4.connect('activate', quit_cb, wTree.get_object("window"), wTree.get_object("vpaned1"), statusIcon)

    statusIcon.connect('activate', activate_icon_cb, None, wTree)
    statusIcon.connect('popup-menu', popup_menu_cb, menu)

    # Set text for all visible widgets (because of i18n)
    wTree.get_object("tool_apply").set_label(_("Install Updates"))
    wTree.get_object("tool_refresh").set_label(_("Refresh"))
    wTree.get_object("tool_select_all").set_label(_("Select All"))
    wTree.get_object("tool_clear").set_label(_("Clear"))
    wTree.get_object("label9").set_text(_("Description"))
    wTree.get_object("label8").set_text(_("Changelog"))

    wTree.get_object("label_success").set_markup("<b>" + _("Your system is up to date") + "</b>")
    wTree.get_object("label_error").set_markup("<b>" + _("Could not refresh the list of updates") + "</b>")
    wTree.get_object("image_success_status").set_from_file("/usr/lib/gooroom/gooroomUpdate/icons/yes.png")
    wTree.get_object("image_error_status").set_from_file("/usr/lib/gooroom/gooroomUpdate/rel_upgrades/failure.png")

    wTree.get_object("vpaned1").set_position(prefs['dimensions_pane_position'])

    fileMenu = wTree.get_object("filemenu")
    fileMenu.set_label(_("_File"))
    fileSubmenu = Gtk.Menu()
    fileMenu.set_submenu(fileSubmenu)
    if os.path.exists("/usr/bin/synaptic-pkexec"):
        synapticMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_PREFERENCES)
        synaptic_icon = "/usr/share/pixmaps/synaptic.png"
        if os.path.exists(synaptic_icon):
            synapticMenuItem.set_image(Gtk.Image.new_from_file(synaptic_icon))

        synapticMenuItem.set_label(_("Synaptic Package Manager"))
        synapticMenuItem.connect("activate", open_synaptic_package_manager)
        fileSubmenu.append(synapticMenuItem)

    closeMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_CLOSE)
    closeMenuItem.set_label(_("Close"))
    closeMenuItem.connect("activate", hide_window, wTree.get_object("window"))
    fileSubmenu.append(closeMenuItem)

    editMenu = wTree.get_object("editmenu")
    editMenu.set_label(_("_Edit"))
    editSubmenu = Gtk.Menu()
    editMenu.set_submenu(editSubmenu)
    prefsMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_PREFERENCES)
    prefsMenuItem.set_label(_("Preferences"))
    prefsMenuItem.connect("activate", open_preferences, treeview_update, statusIcon, wTree)
    editSubmenu.append(prefsMenuItem)

    if os.path.exists("/usr/bin/software-sources") or os.path.exists("/usr/bin/software-properties-gtk") or os.path.exists("/usr/bin/software-properties-kde"):
        if os.system("systemctl status gooroom-agent"):
            sourcesMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_PREFERENCES)
            sourcesMenuItem.set_image(Gtk.Image.new_from_file("/usr/lib/gooroom/gooroomUpdate/icons/software-properties.png"))
            sourcesMenuItem.set_label(_("Software sources"))
            sourcesMenuItem.connect("activate", open_repositories)
            editSubmenu.append(sourcesMenuItem)

    viewMenu = wTree.get_object("viewmenu")
    viewMenu.set_label(_("_View"))
    viewSubmenu = Gtk.Menu()
    viewMenu.set_submenu(viewSubmenu)
    historyMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_INDEX)
    historyMenuItem.set_label(_("History of updates"))
    historyMenuItem.connect("activate", open_history)
    infoMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_DIALOG_INFO)
    infoMenuItem.set_label(_("Information"))
    infoMenuItem.connect("activate", open_information)
    visibleColumnsMenuItem = Gtk.MenuItem(Gtk.STOCK_DIALOG_INFO)
    visibleColumnsMenuItem.set_label(_("Visible columns"))
    visibleColumnsMenu = Gtk.Menu()
    visibleColumnsMenuItem.set_submenu(visibleColumnsMenu)

    typeColumnMenuItem = Gtk.CheckMenuItem(_("Type"))
    typeColumnMenuItem.set_active(prefs["type_column_visible"])
    column7.set_visible(prefs["type_column_visible"])
    typeColumnMenuItem.connect("toggled", setVisibleColumn, column7, "type")
    visibleColumnsMenu.append(typeColumnMenuItem)

    levelColumnMenuItem = Gtk.CheckMenuItem(_("Level"))
    levelColumnMenuItem.set_active(prefs["level_column_visible"])
    column3.set_visible(prefs["level_column_visible"])
    levelColumnMenuItem.connect("toggled", setVisibleColumn, column3, "level")
    visibleColumnsMenu.append(levelColumnMenuItem)

    packageColumnMenuItem = Gtk.CheckMenuItem(_("Package"))
    packageColumnMenuItem.set_active(prefs["package_column_visible"])
    column2.set_visible(prefs["package_column_visible"])
    packageColumnMenuItem.connect("toggled", setVisibleColumn, column2, "package")
    visibleColumnsMenu.append(packageColumnMenuItem)

    oldVersionColumnMenuItem = Gtk.CheckMenuItem(_("Old version"))
    oldVersionColumnMenuItem.set_active(prefs["old_version_column_visible"])
    column4.set_visible(prefs["old_version_column_visible"])
    oldVersionColumnMenuItem.connect("toggled", setVisibleColumn, column4, "old_version")
    visibleColumnsMenu.append(oldVersionColumnMenuItem)

    newVersionColumnMenuItem = Gtk.CheckMenuItem(_("New version"))
    newVersionColumnMenuItem.set_active(prefs["new_version_column_visible"])
    column5.set_visible(prefs["new_version_column_visible"])
    newVersionColumnMenuItem.connect("toggled", setVisibleColumn, column5, "new_version")
    visibleColumnsMenu.append(newVersionColumnMenuItem)

    sizeColumnMenuItem = Gtk.CheckMenuItem(_("Size"))
    sizeColumnMenuItem.set_active(prefs["size_column_visible"])
    column6.set_visible(prefs["size_column_visible"])
    sizeColumnMenuItem.connect("toggled", setVisibleColumn, column6, "size")
    visibleColumnsMenu.append(sizeColumnMenuItem)

    viewSubmenu.append(visibleColumnsMenuItem)

    descriptionsMenuItem = Gtk.CheckMenuItem(_("Show descriptions"))
    descriptionsMenuItem.set_active(prefs["descriptions_visible"])
    descriptionsMenuItem.connect("toggled", setVisibleDescriptions, treeview_update, statusIcon, wTree, prefs)
    viewSubmenu.append(descriptionsMenuItem)

    viewSubmenu.append(historyMenuItem)
    viewSubmenu.append(infoMenuItem)

    helpMenu = wTree.get_object("helpmenu")
    helpMenu.set_label(_("_Help"))
    helpSubmenu = Gtk.Menu()
    helpMenu.set_submenu(helpSubmenu)
    if os.path.exists("/usr/share/help/C/gooroom"):
        helpMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_HELP)
        helpMenuItem.set_label(_("Contents"))
        helpMenuItem.connect("activate", open_help)
        key, mod = Gtk.accelerator_parse("F1")
        helpMenuItem.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        helpSubmenu.append(helpMenuItem)
    aboutMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_ABOUT)
    aboutMenuItem.set_label(_("About"))
    aboutMenuItem.connect("activate", open_about)
    helpSubmenu.append(aboutMenuItem)

    #browser.connect("activate", browser_callback)
    #browser.show()

    if len(sys.argv) > 1:
        showWindow = sys.argv[1]
        if (showWindow == "show"):
            # show 파라미터를 더이상 사용하지 않기 때문에
            # /usr/lib/gooroom/gooroomUpdate/gooroomUpdate.py show & 로 실행되는 경우
            # 업데이트 매니저를 재시작하도록 변경
            command = "/usr/lib/gooroom/gooroomUpdate/gooroomUpdate.py &"
            os.system(command)
            #wTree.get_object("window").show_all()
            #wTree.get_object("vpaned1").set_position(prefs['dimensions_pane_position'])
            #app_hidden = False

    wTree.get_object("window").show_all()
    wTree.get_object("notebook_details").set_current_page(0)

    refresh = RefreshThread(treeview_update, statusIcon, wTree)
    refresh.start()

    auto_refresh = AutomaticRefreshThread(treeview_update, statusIcon, wTree)
    auto_refresh.start()

    Gdk.threads_enter()
    Gtk.main()
    Gdk.threads_leave()

except Exception, detail:
    print detail
    log.writelines(datetime.datetime.now().strftime("%m.%d@%H:%M ") + "-- Exception occurred in main thread: " + str(detail) + "\n")
    log.flush()
    log.close()
