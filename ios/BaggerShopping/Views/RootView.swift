import SwiftUI

struct RootView: View {
    @EnvironmentObject private var navigation: AppNavigation

    var body: some View {
        TabView(selection: $navigation.selectedTab) {
            ShoppingListView()
                .tag(AppTab.shoppingList)
                .tabItem {
                    Label("Liste", systemImage: "cart")
                }

            OffersView()
                .tag(AppTab.offers)
                .tabItem {
                    Label("Tilbud", systemImage: "tag")
                }

            FlyersView()
                .tag(AppTab.flyers)
                .tabItem {
                    Label("Aviser", systemImage: "book.pages")
                }

            StoresView()
                .tag(AppTab.stores)
                .tabItem {
                    Label("Butikker", systemImage: "mappin.and.ellipse")
                }

            SettingsView()
                .tag(AppTab.settings)
                .tabItem {
                    Label("Indstillinger", systemImage: "gearshape")
                }
        }
        .id(navigation.rootResetID)
    }
}
