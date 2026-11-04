package com.assetstudio.data.db

import android.content.Context
import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "cached_assets")
data class CachedAssetEntity(
    @PrimaryKey val id: String,
    val kind: String,                    // model | animation
    val name: String,
    val category: String,
    val sourceFormat: String,
    val localPath: String,
    val bytes: Long,
    val sha256: String,
    val remoteSourcePath: String,
    val validationStatus: String,
    val humanoid: Boolean,
    val targetCharacterId: String,
    val retargetStatus: String,
    val origin: String,                  // catalog | import
    val addedAt: Long,
    val lastOpenedAt: Long
)

/**
 * One row per asset advertised by the published GitHub Pages catalog.
 *
 * This table is the offline copy of `catalog/index.json`: it is written on every
 * successful metadata refresh and read by the library screen, so the app can list
 * assets with no network at all. It never contains credentials — the catalog is a
 * static file tree.
 */
@Entity(tableName = "catalog_assets")
data class CatalogAssetEntity(
    @PrimaryKey val id: String,
    val kind: String,                     // model | animation
    val name: String,
    val category: String,
    val sourceFormat: String,
    val format: String,                   // GLB | GLTF
    val bytes: Long,
    val sha256: String,
    val validationStatus: String,
    val humanoid: Boolean,
    val targetCharacterId: String,
    val retargetStatus: String,
    val clipCount: Int,
    val durationSeconds: Double,
    val updatedAt: String,
    val downloadUrl: String,              // absolute https URL of the GLB
    val manifestUrl: String,              // absolute https URL of catalog/<kind>/<id>.json
    val gltfUrl: String?,                 // absolute https URL of scene.gltf, when published
    val dependenciesJson: String,         // [{"role","path","url","bytes","sha256"}, …]
    val fetchedAt: Long
)

@Dao
interface CachedAssetDao {
    @Query("SELECT * FROM cached_assets ORDER BY lastOpenedAt DESC") fun observeAll(): Flow<List<CachedAssetEntity>>
    @Query("SELECT * FROM cached_assets WHERE kind = :kind ORDER BY name") fun observeByKind(kind: String): Flow<List<CachedAssetEntity>>
    @Query("SELECT * FROM cached_assets WHERE id = :id") suspend fun get(id: String): CachedAssetEntity?
    @Query("SELECT * FROM cached_assets WHERE name LIKE '%' || :q || '%' OR id LIKE '%' || :q || '%' ORDER BY name")
    fun search(q: String): Flow<List<CachedAssetEntity>>
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun upsert(a: CachedAssetEntity)
    @Query("UPDATE cached_assets SET lastOpenedAt = :t WHERE id = :id") suspend fun touch(id: String, t: Long)
    @Query("DELETE FROM cached_assets WHERE id = :id") suspend fun delete(id: String)
    @Query("SELECT SUM(bytes) FROM cached_assets") fun totalBytes(): Flow<Long?>
    @Query("SELECT COUNT(*) FROM cached_assets") fun count(): Flow<Int>
}

@Dao
interface CatalogAssetDao {
    @Query("SELECT * FROM catalog_assets ORDER BY kind, name") fun observeAll(): Flow<List<CatalogAssetEntity>>
    @Query("SELECT * FROM catalog_assets WHERE kind = :kind ORDER BY name") fun observeByKind(kind: String): Flow<List<CatalogAssetEntity>>
    @Query("SELECT * FROM catalog_assets WHERE id = :id") suspend fun get(id: String): CatalogAssetEntity?
    @Query("SELECT * FROM catalog_assets WHERE kind = :kind ORDER BY name") suspend fun byKind(kind: String): List<CatalogAssetEntity>
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun upsertAll(items: List<CatalogAssetEntity>)
    @Query("DELETE FROM catalog_assets WHERE kind = :kind") suspend fun deleteKind(kind: String)
    @Query("SELECT MAX(fetchedAt) FROM catalog_assets") fun lastFetchedAt(): Flow<Long?>
    @Query("SELECT COUNT(*) FROM catalog_assets") fun count(): Flow<Int>

    /** Replace one kind atomically so a partial refresh can never leave a half catalog. */
    @Transaction
    suspend fun replaceKind(kind: String, items: List<CatalogAssetEntity>) {
        deleteKind(kind)
        if (items.isNotEmpty()) upsertAll(items)
    }
}

@Database(entities = [CachedAssetEntity::class, CatalogAssetEntity::class], version = 2,
          exportSchema = false)
abstract class AssetDatabase : RoomDatabase() {
    abstract fun cachedAssetDao(): CachedAssetDao
    abstract fun catalogAssetDao(): CatalogAssetDao
    companion object {
        @Volatile private var instance: AssetDatabase? = null
        fun get(context: Context): AssetDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context.applicationContext,
                AssetDatabase::class.java, "asset_studio.db")
                .fallbackToDestructiveMigration().build().also { instance = it }
        }
    }
}
