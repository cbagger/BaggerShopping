import SwiftUI

struct RootView: View {
    var body: some View {
        TabView {
            ShoppingListView()
                .tabItem {
                    Label("Liste", systemImage: "cart")
                }

            OffersView()
                .tabItem {
                    Label("Tilbud", systemImage: "tag")
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
