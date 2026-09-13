//
//  Mirage Wallpaper
//
//  Copyright © 2026 王孝慈. All rights reserved.
//

import Foundation
import Observation

final class WallpaperSizeCache: @unchecked Sendable {
    static let shared = WallpaperSizeCache()
    static let didInvalidate = Notification.Name("MirageWallpaperSizesInvalidated")

    private final class Request {
        let directory: URL
        let operation = BlockOperation()
        var consumers: [UUID: (Int?) -> Void] = [:]

        init(directory: URL) { self.directory = directory }
    }

    private let lock = NSLock()
    private let queue: OperationQueue
    private let measure: (URL, () -> Bool) -> Int?
    private var values: [URL: Int] = [:]
    private var requests: [URL: Request] = [:]
    private var owners: [UUID: Request] = [:]

    init(measure: @escaping (URL, () -> Bool) -> Int? = { directory, cancelled in
        (try? directory.directoryTotalAllocatedSize(includingSubfolders: true, isCancelled: cancelled)) ?? nil
    }) {
        self.measure = measure
        queue = OperationQueue()
        queue.name = "cn.laobamac.Mirage.wallpaper.sizes"
        queue.qualityOfService = .utility
        queue.maxConcurrentOperationCount = 2
    }

    func cachedSize(at directory: URL) -> Int? {
        lock.lock()
        defer { lock.unlock() }
        return values[directory.standardizedFileURL]
    }

    @discardableResult
    func load(at directory: URL, completion: @escaping (Int?) -> Void) -> UUID {
        let token = UUID()
        let directory = directory.standardizedFileURL
        lock.lock()
        if let value = values[directory] {
            lock.unlock()
            completion(value)
            return token
        }
        let request: Request
        let isNew: Bool
        if let existing = requests[directory] {
            request = existing
            isNew = false
        } else {
            request = Request(directory: directory)
            requests[directory] = request
            isNew = true
        }
        request.consumers[token] = completion
        owners[token] = request
        lock.unlock()
        if isNew {
            request.operation.addExecutionBlock { [weak self, weak request] in
                guard let self, let request else { return }
                let value = self.measure(directory) { request.operation.isCancelled }
                self.finish(request, value: value)
            }
            queue.addOperation(request.operation)
        }
        return token
    }

    func cancel(_ token: UUID) {
        lock.lock()
        defer { lock.unlock() }
        guard let request = owners.removeValue(forKey: token) else { return }
        request.consumers[token] = nil
        if request.consumers.isEmpty {
            request.operation.cancel()
            if requests[request.directory] === request { requests[request.directory] = nil }
        }
    }

    func size(at directory: URL) -> Int {
        if let cached = cachedSize(at: directory) { return cached }
        let ready = DispatchSemaphore(value: 0)
        var result = 0
        load(at: directory) { value in
            result = value ?? 0
            ready.signal()
        }
        ready.wait()
        return result
    }

    func invalidate() {
        lock.lock()
        values.removeAll()
        let pending = Array(requests.values)
        requests.removeAll()
        owners.removeAll()
        let completions = pending.flatMap { request -> [(Int?) -> Void] in
            request.operation.cancel()
            let result = Array(request.consumers.values)
            request.consumers.removeAll()
            return result
        }
        lock.unlock()
        completions.forEach { $0(nil) }
        DispatchQueue.main.async {
            NotificationCenter.default.post(name: Self.didInvalidate, object: self)
        }
    }

    private func finish(_ request: Request, value: Int?) {
        lock.lock()
        guard requests[request.directory] === request else { lock.unlock(); return }
        requests[request.directory] = nil
        if let value, !request.operation.isCancelled { values[request.directory] = value }
        let completions = Array(request.consumers.values)
        for token in request.consumers.keys { owners[token] = nil }
        request.consumers.removeAll()
        lock.unlock()
        completions.forEach { $0(value) }
    }
}

@Observable
final class WallpaperSizeModel {
    private(set) var bytes: Int?
    @ObservationIgnored private let cache: WallpaperSizeCache
    @ObservationIgnored private var request: UUID?
    @ObservationIgnored private var generation = UUID()

    init(cache: WallpaperSizeCache = .shared) { self.cache = cache }

    func load(_ directory: URL) {
        cancel()
        bytes = cache.cachedSize(at: directory)
        guard bytes == nil else { return }
        let token = generation
        request = cache.load(at: directory) { [weak self] value in
            DispatchQueue.main.async {
                guard let self, self.generation == token else { return }
                self.request = nil
                self.bytes = value ?? 0
            }
        }
    }

    func cancel() {
        generation = UUID()
        if let request { cache.cancel(request) }
        request = nil
    }

    deinit {
        if let request { cache.cancel(request) }
    }
}
