import SwiftUI

@main
struct BaggerShoppingApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var appModel = AppModel()
    @StateObject private var navigation = AppNavigation()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(appModel)
                .environmentObject(navigation)
                .onAppear {
                    // Paint the last confirmed Samsung list immediately. The
                    // authoritative refresh continues in the background, so a
                    // cold network/QNAP path no longer leaves startup blank.
                    appModel.restoreCachedShoppingList()
                }
                .task {
                    async let bootstrap: Void = appModel.bootstrap()
                    async let flyerPush: Void = appModel.flyerPush.bootstrap()
                    _ = await (bootstrap, flyerPush)
                }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background:
                navigation.didEnterBackground()
                appModel.continuePendingChecksInBackground()
            case .active:
                _ = navigation.resetAfterLongInactivityIfNeeded()
                Task {
                    await appModel.resumeFromBackground()
                }
            case .inactive:
                break
            @unknown default:
                break
            }
        }
    }
}
