import SwiftUI

struct RootView: View {
    @EnvironmentObject private var navigation: AppNavigation
    @EnvironmentObject private var model: AppModel
    @StateObject private var offerAddActivity = OfferAddActivity.shared

    var body: some View {
        if !model.tokenConfigured || model.onboardingRequired {
            OnboardingView()
        } else {
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
            .overlay(alignment: .top) {
                if let message = offerAddActivity.phase.message {
                    HStack(spacing: 10) {
                        if offerAddActivity.phase.showsProgress {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        }
                        Text(message)
                            .font(.subheadline.weight(.semibold))
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 11)
                    .background(.ultraThinMaterial, in: Capsule())
                    .shadow(radius: 8, y: 3)
                    .padding(.top, 8)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .allowsHitTesting(false)
                }
            }
            .animation(.snappy(duration: 0.2), value: offerAddActivity.phase)
            .onChange(of: model.shoppingList?.items.count) { oldValue, newValue in
                guard offerAddActivity.phase == .adding,
                      let newValue,
                      newValue > (oldValue ?? 0) else { return }
                offerAddActivity.markAdded()
            }
        }
    }
}
