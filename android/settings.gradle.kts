pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories { google(); mavenCentral() }
}
rootProject.name = "3D Asset Studio"
include(":app")
include(":core")
include(":data")
include(":asset-library")
include(":renderer")
include(":skeleton")
include(":animation")
include(":inspector")
