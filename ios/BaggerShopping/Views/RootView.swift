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
            .overlay {
                if let message = offerAddActivity.phase.message {
                    VStack(spacing: 12) {
                        if offerAddActivity.phase.showsProgress {
                            ProgressView()
                                .controlSize(.regular)
                        } else {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.title2)
                                .foregroundStyle(.green)
                        }
                        Text(message)
                            .font(.subheadline.weight(.semibold))
                            .multilineTextAlignment(.center)
                    }
                    .padding(.horizontal, 22)
                    .padding(.vertical, 18)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .shadow(radius: 12, y: 4)
                    .transition(.scale(scale: 0.96).combined(with: .opacity))
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
