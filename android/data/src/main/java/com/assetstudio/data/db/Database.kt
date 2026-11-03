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
    val origin: String,                  // library | import
    val addedAt: Long,
    val lastOpenedAt: Long
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

@Database(entities = [CachedAssetEntity::class], version = 1, exportSchema = true)
abstract class AssetDatabase : RoomDatabase() {
    abstract fun cachedAssetDao(): CachedAssetDao
    companion object {
        @Volatile private var instance: AssetDatabase? = null
        fun get(context: Context): AssetDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context.applicationContext,
                AssetDatabase::class.java, "asset_studio.db")
                .fallbackToDestructiveMigration().build().also { instance = it }
        }
    }
}
