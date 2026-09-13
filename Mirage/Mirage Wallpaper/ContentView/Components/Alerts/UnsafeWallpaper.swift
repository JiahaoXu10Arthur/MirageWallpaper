//
//  Mirage Wallpaper
//
//  Copyright © 2026 王孝慈. All rights reserved.
//

import SwiftUI

struct UnsafeWallpaper: View {
    @Environment(\.dismiss) var dismiss

    let request: ContentViewModel.PendingTrustRequest

    private var wallpaper: WEWallpaper { request.wallpaper }

    @State var seconds: Int = 5
    @State private var continued = false

    var typeStringDict: [String : String] =
    [
        "web": "网页",
        "application": "应用程序"
    ]

    init(request: ContentViewModel.PendingTrustRequest) {
        self.request = request
    }

    var body: some View {
        VStack(spacing: 0) {
            Text(L("正在打开%@类壁纸", L(typeStringDict[wallpaper.project.type.lowercased()] ?? "未知")))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
                .font(.title2)
            Divider()
            HStack(spacing: 20) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, .red)
                    .shadow(radius: 6)
                    .frame(maxWidth: 100)
                VStack(alignment: .leading, spacing: 10) {
                    Text(L("你即将把以下%@类文件作为壁纸运行：", L(typeStringDict[wallpaper.project.type.lowercased()] ?? "未知来源")))
                    Text(wallpaper.resolvedEntryURL.path(percentEncoded: false)).bold()
                    Text(L("Mirage 无法控制该文件的行为，网页壁纸可能包含可执行脚本。请确认它来自可信来源后再继续。"))
                    Text(seconds > 0 ? L("请等待 %d 秒。", seconds) : L("请注意潜在的恶意代码风险。"))
                }
                .frame(maxWidth: .infinity)
            }
            .frame(maxHeight: .infinity)
            .padding(.horizontal)
            Divider()
            HStack(spacing: 12) {
                Button {
                    continueApplying(persistently: true)
                } label: {
                    Text(L("信任并继续"))
                        .padding(.horizontal, 10)
                }
                .animation(.default, value: seconds)
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .disabled(seconds > 0 ? true : false)
                Button {
                    continueApplying(persistently: false)
                } label: {
                    Text(L("仅本次继续"))
                        .padding(.horizontal, 10)
                }
                .disabled(seconds > 0 ? true : false)
                Button {
                    dismiss()
                } label: {
                    Text(L("取消"))
                        .padding(.horizontal, 10)
                }
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .onAppear {
            let _ = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { timer in
                if self.seconds <= 0 {
                    timer.invalidate()
                } else {
                    self.seconds -= 1
                }
            }
        }
        .onDisappear {
            guard !continued else { return }
            let model = AppDelegate.shared.wallpaperViewModel
            if case .applyOnDisplay(let displayID) = request.action {
                model.cancelPendingPreview(wallpaperID: wallpaper.id, onDisplay: displayID)
            } else {
                model.cancelPendingPreview(wallpaperID: wallpaper.id)
            }
        }
    }

    private func continueApplying(persistently: Bool) {
        continued = true
        let viewModel = AppDelegate.shared.wallpaperViewModel
        if persistently {
            viewModel.trust(wallpaper)
        } else {
            WallpaperViewModel.trustForSession(wallpaper)
        }
        switch request.action {
        case .applyToCurrent:
            viewModel.currentWallpaper = wallpaper
        case .applyOnDisplay(let displayID):
            viewModel.applyOnDisplay(wallpaper, displayID: displayID)
        case .applyToAllDisplays:
            viewModel.applyToAllDisplays(wallpaper)
        }
        dismiss()
    }
}
