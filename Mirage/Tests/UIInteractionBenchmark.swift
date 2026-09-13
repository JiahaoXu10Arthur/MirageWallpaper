//
//  Mirage Wallpaper
//
//  Copyright © 2026 王孝慈. All rights reserved.
//

import AppKit
import SwiftUI
@testable import Mirage_Wallpaper

@main
@MainActor
private struct UIInteractionBenchmark {
    static func capture(_ view: NSView, at url: URL) throws {
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        try bitmap.representation(using: .png, properties: [:])?.write(to: url)
    }

    static func report(_ name: String, _ samples: [Double]) {
        let sorted = samples.sorted()
        func percentile(_ value: Double) -> Double {
            sorted[min(sorted.count - 1, Int(Double(sorted.count - 1) * value))]
        }
        let values: [String: Any] = ["case": name, "samples": samples.count,
            "p50_ms": percentile(0.50), "p95_ms": percentile(0.95),
            "p99_ms": percentile(0.99), "max_ms": sorted.last!]
        let data = try! JSONSerialization.data(withJSONObject: values, options: [.sortedKeys])
        print(String(decoding: data, as: UTF8.self))
    }

    static func main() async throws {
        let directory = Bundle.main.resourceURL!.appending(path: "BenchmarkFixtures")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        UserDefaults.standard.set(directory.appending(path: "Imported").path, forKey: "CustomImportedDirectory")
        UserDefaults.standard.set(directory.appending(path: "Workshop").path, forKey: "CustomWorkshopDirectory")
        NSApplication.shared.setActivationPolicy(.accessory)
        NSApplication.shared.finishLaunching()
        let settings = GlobalSettingsViewModel()
        let playback = WallpaperViewModel()
        let library = (0..<2_000).map { index in
            WEWallpaper(using: WEProject(file: "", preview: "", title: "Wallpaper \(index)", type: "video"),
                        where: directory.appending(path: String(index)))
        }
        #if MIRAGE_UI_BASELINE
        let content = AppDelegate.shared.contentViewModel
        content.wallpapers = library
        #else
        let content = ContentViewModel(observeLibrary: false)
        content.librarySnapshot = ContentViewModel.LibrarySnapshot(library)
        #endif
        let manager = PlaylistManager(storageURL: directory.appending(path: "playlist.json"))
        manager.load(saved: Playlist(items: library.prefix(50).map {
            PlaylistItem(wallpaperID: $0.id, addedAt: Date())
        }), into: 0)
        func playlist(_ index: Int) -> AnyView {
            #if MIRAGE_UI_BASELINE
            AnyView(PlaylistStrip(manager: manager, wallpaperViewModel: playback,
                screen: 0, selectedItemID: .constant(library[index].id)).environment(settings))
            #else
            AnyView(PlaylistStrip(manager: manager, wallpaperViewModel: playback, contentViewModel: content,
                screen: 0, selectedItemID: .constant(library[index].id)).environment(settings))
            #endif
        }
        let window = NSWindow(contentRect: NSRect(x: 50, y: 50, width: 960, height: 420),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let host = NSHostingView(rootView: playlist(0))
        host.sizingOptions = []
        window.contentView = host
        window.orderFront(nil)
        defer { window.close() }
        try await Task.sleep(for: .milliseconds(500))
        try capture(host, at: directory.appending(path: "playlist.png"))
        var selectionSamples: [Double] = []
        for index in 0..<40 {
            let start = DispatchTime.now().uptimeNanoseconds
            host.rootView = playlist(index % 10)
            host.needsLayout = true
            host.layoutSubtreeIfNeeded()
            host.displayIfNeeded()
            selectionSamples.append(Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000)
            try await Task.sleep(for: .milliseconds(8))
        }
        report("playlist_2000_wallpapers_50_entries", Array(selectionSamples.dropFirst(5)))

        let propertyDirectory = directory.appending(path: "Properties")
        try FileManager.default.createDirectory(at: propertyDirectory, withIntermediateDirectories: true)
        let properties = Dictionary(uniqueKeysWithValues: (0..<300).map { (index: Int) in
            ("property-\(index)", WEProjectProperty(type: "slider", value: .number(0),
                text: "Property \(index)", order: index))
        })
        let project = WEProject(file: "video.mp4", general: WEProjectGeneral(properties: .init(items: properties)),
                                preview: "", title: "Property benchmark", type: "video")
        try JSONEncoder().encode(project).write(to: propertyDirectory.appending(path: "project.json"))
        try Data().write(to: propertyDirectory.appending(path: "video.mp4"))
        let wallpaper = WEWallpaper.load(from: propertyDirectory)
        playback.selectedDisplayKey = DisplayKey(rawValue: "benchmark-display")
        playback.assign(wallpaper, to: playback.selectedDisplayKey)
        precondition(playback.currentWallpaper.id == wallpaper.id &&
                     wallpaper.project.general?.properties?.items["property-0"] != nil)
        host.rootView = AnyView(ScrollView {
            PropertyEditor(wallpaper: wallpaper).environment(playback).padding()
        }.frame(width: 320).environment(settings))
        try await Task.sleep(for: .milliseconds(300))
        try capture(host, at: directory.appending(path: "properties.png"))
        var propertySamples: [Double] = []
        for value in 1...60 {
            let start = DispatchTime.now().uptimeNanoseconds
            playback.setProperty(key: "property-0", value: .number(Double(value)))
            host.needsLayout = true
            host.layoutSubtreeIfNeeded()
            host.displayIfNeeded()
            propertySamples.append(Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000)
            precondition(playback.runtime.propertyOverrides["property-0"] == .number(Double(value)))
            try await Task.sleep(for: .milliseconds(8))
        }
        report("property_panel_300_controls", Array(propertySamples.dropFirst(5)))
        playback.saveRuntime()
        playback.flushPendingSaves()
    }
}
