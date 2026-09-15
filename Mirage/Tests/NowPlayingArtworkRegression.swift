import AppKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

// The standalone cache test never queries the user's media session.
@_cdecl("MirageCopyNowPlayingJSON")
func copyNowPlayingStub() -> UnsafeMutablePointer<CChar>? { nil }
@_cdecl("MirageFreeNowPlayingJSON")
func freeNowPlayingStub(_ pointer: UnsafeMutablePointer<CChar>) {}

@main
struct NowPlayingArtworkRegression {
    static func check(_ value: @autoclosure () -> Bool, _ message: String) {
        guard value() else { fatalError(message) }
    }

    static func artwork(red: CGFloat, blue: CGFloat) -> Data {
        let context = CGContext(data: nil, width: 256, height: 256,
                                bitsPerComponent: 8, bytesPerRow: 1024,
                                space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(red: red, green: 0.1, blue: blue, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 256, height: 256))
        let data = NSMutableData()
        let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, context.makeImage()!, nil)
        check(CGImageDestinationFinalize(destination), "fixture encoding failed")
        return data as Data
    }

    static func main() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("mirage-artwork-test-\(UUID())", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        var paletteCalls = 0
        var cache = NowPlayingArtworkCache(directory: directory) { image in
            paletteCalls += 1
            return NowPlayingArtworkCache.palette(image)
        }
        let red = artwork(red: 0.9, blue: 0.1)
        let blue = artwork(red: 0.1, blue: 0.9)
        let first = cache.persistArtwork(red, mimeType: "image/png")!
        check(first.colors.count == 5, "palette missing")
        let persisted = try Data(contentsOf: URL(fileURLWithPath: first.url))
        check(persisted == red, "persisted image changed")
        for _ in 0..<100 {
            let repeated = cache.persistArtwork(red, mimeType: "IMAGE/PNG")!
            check(repeated.url == first.url && repeated.colors == first.colors, "cache changed payload")
        }
        check(paletteCalls == 1, "unchanged artwork was processed again")
        let second = cache.persistArtwork(blue, mimeType: "image/png")!
        check(second.url != first.url && second.colors != first.colors, "changed image returned stale artwork")
        check(paletteCalls == 2, "changed artwork was not processed")
        _ = cache.persistArtwork(red, mimeType: "image/png")
        check(paletteCalls == 3, "cache retained more than the most recent result")
        cache.clear()
        _ = cache.persistArtwork(red, mimeType: "image/png")
        check(paletteCalls == 4, "clear did not reset cache")
        try FileManager.default.removeItem(atPath: first.url)
        _ = cache.persistArtwork(red, mimeType: "image/png")
        check(FileManager.default.fileExists(atPath: first.url), "deleted artwork was not restored")
        check(paletteCalls == 5, "missing file incorrectly used cached result")
        let mimeChanged = cache.persistArtwork(red, mimeType: "image/jpeg")!
        check(mimeChanged.url != first.url && mimeChanged.url.hasSuffix(".jpg"), "MIME extension ignored")
        check(paletteCalls == 6, "MIME change reused wrong result")
        check(cache.persistArtwork(Data([0, 1, 2]), mimeType: nil) == nil, "invalid image accepted")
        _ = cache.persistArtwork(red, mimeType: "image/jpeg")
        check(paletteCalls == 6, "failed image poisoned last successful result")

        let blocked = directory.appendingPathComponent("not-a-directory")
        try Data([0]).write(to: blocked)
        var failedCache = NowPlayingArtworkCache(directory: blocked)
        check(failedCache.persistArtwork(red, mimeType: "image/png") == nil, "write failure accepted")
        print("PASS: repeated artwork, changed pixels/MIME, bounded retention, reset, missing file and failures")
    }
}
