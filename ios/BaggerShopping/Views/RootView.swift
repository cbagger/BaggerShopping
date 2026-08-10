import SwiftUI

struct RootView: View {
    var body: some View {
        TabView {
            ShoppingListView()
                .tabItem {
                    Label("Liste", systemImage: "cart")
                }

            StoresView()
                .tabItem {
                    Label("Butikker", systemImage: "mappin.and.ellipse")
                }

            SettingsView()
                .tabItem {
                    Label("Indstillinger", systemImage: "gearshape")
                }
        }
    }
}
