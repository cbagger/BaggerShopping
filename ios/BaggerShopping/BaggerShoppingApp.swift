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
                .task {
                    await appModel.bootstrap()
                    await appModel.flyerPush.bootstrap()
                }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background:
                navigation.didEnterBackground()
            case .active:
                _ = navigation.resetAfterLongInactivityIfNeeded()
                Task {
                    await appModel.refresh()
                }
            case .inactive:
                break
            @unknown default:
                break
            }
        }
    }
}
